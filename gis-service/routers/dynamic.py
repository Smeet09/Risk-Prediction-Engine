"""
dynamic.py — FastAPI Router for Dynamic Risk Prediction
=======================================================
Mirrors the susceptibility pipeline:
  1. Computes dynamic risk (TOPMODEL/SCS-CN physics)
  2. Saves shapefile locally  (dynamic_output/{code}_dynamic_class.shp)
  3. Imports into PostGIS     (dynamic_risk_{code} table — same schema as susceptibility_{code})
  4. Stores metadata + GeoJSON in dynamic_risk_results table
  5. Backend reads from PostGIS via the result endpoint

Endpoints:
  POST /dynamic/predict
  GET  /dynamic/result/{region_id}/{disaster_code}/{target_date}
  GET  /dynamic/history/{region_id}/{disaster_code}
  GET  /dynamic/available-dates/{region_id}/{disaster_code}/{state}
"""

import json
import re
import traceback
import sys
import httpx
from datetime import datetime
from pathlib import Path

import numpy as np
import geopandas as gpd
import shapely.geometry
from shapely.geometry import shape
import psycopg2
import psycopg2.extras
import rasterio
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from config import settings
from scripts.dynamic_core import (
    load_weather_from_db,
    build_grid,
    aggregate_susceptibility,
    aggregate_lulc,
    combine_and_classify,
    raster_to_geojson,
    get_class_stats,
    get_lulc_path_from_db,
    get_susceptibility_path_from_db,
    RISK_LABELS,
    RISK_COLORS,
)

router = APIRouter()

CLASS_LABELS = {1: "Very Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very High"}
NODATA_INT   = -9999


# ─── Pydantic schemas ─────────────────────────────────────────────────────────

class DynamicPredictRequest(BaseModel):
    job_id:          str
    region_id:       str
    disaster_code:   str          # "landslide" | "flood"
    state:           str
    country:         str  = ""
    district:        str  = ""
    target_date:     str          # YYYY-MM-DD
    antecedent_days: int  = 10


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _db_conn():
    return psycopg2.connect(settings.DATABASE_URL)

def _get_engine():
    return create_engine(settings.DATABASE_URL)


def _ensure_results_table():
    """Create dynamic_risk_results metadata table if needed."""
    ddl = """
    CREATE TABLE IF NOT EXISTS dynamic_risk_results (
        id              BIGSERIAL PRIMARY KEY,
        region_id       TEXT NOT NULL,
        disaster_code   TEXT NOT NULL,
        target_date     DATE NOT NULL,
        class_stats     JSONB,
        risk_geojson    JSONB,
        composite_mean  FLOAT,
        trigger_method  TEXT,
        warning_summary JSONB,
        tif_path        TEXT,
        shp_path        TEXT,
        physics_meta    JSONB,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (region_id, disaster_code, target_date)
    );
    CREATE INDEX IF NOT EXISTS idx_dynrisk_region_code_date
        ON dynamic_risk_results (region_id, disaster_code, target_date DESC);
    """
    conn = _db_conn()
    try:
        with conn:
            conn.cursor().execute(ddl)
    finally:
        conn.close()


def _ensure_postgis_table(engine, disaster_code: str):
    """
    Create dynamic_risk_{code} PostGIS table if not exists.
    Same schema as susceptibility_{code} so the same frontend query works.
    """
    safe = re.sub(r"[^a-z0-9_]", "_", disaster_code.lower())
    table = f"dynamic_risk_{safe}"
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id             BIGSERIAL PRIMARY KEY,
                region_id      TEXT NOT NULL,
                country        TEXT DEFAULT '',
                state          TEXT DEFAULT '',
                district       TEXT DEFAULT '',
                target_date    DATE,
                class_id       INTEGER,
                risk_class     TEXT,
                geom           GEOMETRY(MULTIPOLYGON, 4326),
                created_at     TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_{table}_region
                ON {table} (region_id);
            CREATE INDEX IF NOT EXISTS idx_{table}_date
                ON {table} (target_date);
        """))
    return table


def _log_job(job_id: str, status: str, progress: int, msg: str):
    try:
        conn = _db_conn()
        with conn:
            conn.cursor().execute(
                """UPDATE jobs
                   SET status=%s, progress=%s,
                       log = log || E'\\n' || %s,
                       updated_at=NOW()
                   WHERE id=%s::uuid""",
                (status, progress, msg, job_id)
            )
    except Exception as e:
        print(f"  [JobLog] {e}")
    finally:
        conn.close()

    # Notify backend via webhook for real-time WebSocket broadcast
    # We use a tiny timeout to ensure we don't hang if the backend is busy
    try:
        url = f"{settings.BACKEND_URL}/api/jobs/{job_id}/progress"
        # Fire and forget (almost)
        with httpx.Client(timeout=0.5) as client:
            client.patch(url, json={
                "status": status,
                "progress": progress,
                "log": msg
            })
    except Exception as e:
        # Non-fatal
        print(f"  [JobNotify] Skip notification: {e}")


# ─── Core: save to shapefile + PostGIS ────────────────────────────────────────

def _save_to_shp_and_postgis(
    risk: np.ndarray,
    grid_meta: dict,
    disaster_code: str,
    region_id: str,
    country: str,
    state: str,
    district: str,
    target_date: str,
    engine,
    output_dir: Path,
) -> tuple[str | None, str | None, dict]:
    """
    Vectorise risk raster → GeoDataFrame → save .shp + push to PostGIS.
    Returns (shp_path, geojson_dict, class_stats).
    """
    from rasterio.transform import from_bounds

    transform = grid_meta["transform"]
    import rasterio.features

    rows = []
    for cls_id in range(1, 6):
        mask = (risk == cls_id).astype(np.uint8)
        if not mask.any():
            continue
        for geom_dict, _ in rasterio.features.shapes(mask, mask=mask, transform=transform):
            geom = shape(geom_dict)
            if geom.geom_type == "Polygon":
                geom = shapely.geometry.MultiPolygon([geom])
            rows.append({
                "region_id":  region_id,
                "country":    country  or "",
                "state":      state    or "",
                "district":   district or "",
                "target_date": target_date,
                "class_id":   cls_id,
                "risk_class": CLASS_LABELS[cls_id],
                "geometry":   geom,
            })

    if not rows:
        print("  [DynSave] No risk polygons — skipping SHP/PostGIS save")
        return None, {"type": "FeatureCollection", "features": []}, {}

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")

    # ── 1. Save shapefile (Mirrors susceptibility naming) ──────────────────────
    shp_path = None
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        shp_file = output_dir / f"{disaster_code}_class.shp"
        # Rename column to match susceptibility schema precisely
        gdf.rename(columns={"risk_class": "susc_class"}).to_file(str(shp_file))
        shp_path = str(shp_file)
        print(f"  [DynSave] SHP saved → {shp_file.name}")
    except Exception as e:
        print(f"  [DynSave] SHP save warning: {e}")

    # ── 2. PostGIS ─────────────────────────────────────────────────────────────
    try:
        table = _ensure_postgis_table(engine, disaster_code)
        # Delete previous results for this region+date
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {table} WHERE region_id=:rid AND target_date=:dt"),
                {"rid": region_id, "dt": target_date}
            )
        gdf.rename_geometry("geom").to_postgis(
            table, engine, if_exists="append", index=False, chunksize=500
        )
        print(f"  [DynSave] PostGIS → {table} ({len(rows)} features)")
    except Exception as e:
        print(f"  [DynSave] PostGIS warning (non-fatal): {e}")

    # ── 3. Build GeoJSON for metadata storage ──────────────────────────────────
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "class_id":   int(r["class_id"]),
                    "risk_class": r["risk_class"],
                    "color":      RISK_COLORS.get(int(r["class_id"]), "#ccc"),
                },
                "geometry": r["geometry"].__geo_interface__,
            }
            for r in rows
        ],
    }

    class_stats = get_class_stats(risk)
    return shp_path, geojson, class_stats


# ─── Metadata store ───────────────────────────────────────────────────────────

def _store_result_metadata(region_id, disaster_code, target_date,
                            class_stats, geojson, composite_mean,
                            trigger_method, physics_meta,
                            shp_path=None, tif_path=None):
    conn = _db_conn()
    try:
        with conn:
            conn.cursor().execute("""
                INSERT INTO dynamic_risk_results
                    (region_id, disaster_code, target_date,
                     class_stats, risk_geojson, composite_mean,
                     trigger_method, physics_meta, shp_path, tif_path)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (region_id, disaster_code, target_date)
                DO UPDATE SET
                    class_stats    = EXCLUDED.class_stats,
                    risk_geojson   = EXCLUDED.risk_geojson,
                    composite_mean = EXCLUDED.composite_mean,
                    trigger_method = EXCLUDED.trigger_method,
                    physics_meta   = EXCLUDED.physics_meta,
                    shp_path       = EXCLUDED.shp_path,
                    tif_path       = EXCLUDED.tif_path,
                    created_at     = NOW()
            """, (
                region_id, disaster_code, target_date,
                json.dumps(class_stats),
                json.dumps(geojson),
                float(composite_mean) if composite_mean is not None else None,
                trigger_method,
                json.dumps(physics_meta) if physics_meta else None,
                shp_path, tif_path,
            ))
    finally:
        conn.close()


# ─── Helper: find output dir (mirrors susceptibility pattern) ─────────────────

def _get_dynamic_output_dir(data_root: Path, disaster_code: str,
                              country: str, state: str, district: str) -> Path:
    def _safe(s):
        if not s or str(s).strip() in ("", "none", "None"):
            return "_state_level"
        return re.sub(r"[^a-zA-Z0-9_-]", "_", str(s))
    # Mirrored exactly with susceptibility output dir
    return (data_root / _safe(disaster_code) / _safe(country)
            / _safe(state) / _safe(district) / "susceptibility_output")


def _find_slope_tif(region_id: str) -> str | None:
    try:
        conn = _db_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT output_dir FROM topographic_features WHERE region_id=%s LIMIT 1",
            (region_id,)
        )
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row[0]:
            p = Path(row[0]) / "slope.tif"
            return str(p) if p.exists() else None
    except Exception:
        pass
    return None


def _find_class_tif(susc_tif: str | None, disaster_code: str) -> str | None:
    if not susc_tif:
        return None
    p = Path(susc_tif)
    cls_tif = p.parent / f"{disaster_code}_class.tif"
    return str(cls_tif) if cls_tif.exists() else susc_tif


# ─── Main pipeline ────────────────────────────────────────────────────────────

def _run_dynamic_pipeline(
    job_id: str, region_id: str, disaster_code: str,
    state: str, country: str, district: str,
    target_date: str, antecedent_days: int
):
    def dprint(msg):
        print(msg, flush=True)

    try:
        _ensure_results_table()
        engine    = _get_engine()
        data_root = settings.data_root_path

        dprint(f"\n{'='*62}")
        dprint(f"  DYNAMIC RISK  |  {disaster_code.upper()}  |  {state}  |  {target_date}")
        dprint(f"{'='*62}")

        # ── [1] Master Grid Resolution Detection ──────────────────────────────
        _log_job(job_id, "processing", 5, "[1/7] Detecting high-res master grid…")
        susc_tif  = get_susceptibility_path_from_db(settings.DATABASE_URL, region_id, disaster_code)
        susc_path = _find_class_tif(susc_tif, disaster_code)

        master_meta = None
        if susc_path and Path(susc_path).exists():
            with rasterio.open(susc_path) as src:
                master_meta = {
                    "nrows": src.height,
                    "ncols": src.width,
                    "transform": src.transform,
                    "crs": src.crs,
                }
        
        # ── [2] Weather from DB ───────────────────────────────────────────────
        _log_job(job_id, "processing", 15, "[2/7] Loading weather data from DB…")
        weather_df = load_weather_from_db(
            settings.DATABASE_URL, state, target_date, antecedent_days
        )

        # ── [3] 2km Grid computation ──────────────────────────────────────────
        _log_job(job_id, "processing", 25, "[3/7] Computing 2km trigger model…")
        weather_df, grid_meta_2km = build_grid(weather_df)

        if disaster_code == "landslide":
            from scripts.landslide_dynamic_db import compute_landslide_trigger
            slope_path = _find_slope_tif(region_id)
            trigger_score_2km, trigger_method, physics_meta = compute_landslide_trigger(
                weather_df, grid_meta_2km, slope_path=slope_path
            )
        else:
            from scripts.flood_dynamic_db import compute_flood_trigger
            trigger_score_2km, trigger_method, physics_meta = compute_flood_trigger(
                weather_df, grid_meta_2km
            )

        # ── [4] High-Res Alignment & Upsampling ────────────────────────────────
        if master_meta:
            _log_job(job_id, "processing", 45, "[4/7] Upsampling trigger to master grid (Pixel Size)…")
            from scripts.dynamic_core import upsample_array
            trigger_score = upsample_array(trigger_score_2km, grid_meta_2km["transform"], master_meta)
            grid_meta = master_meta
            high_res = True
        else:
            _log_job(job_id, "processing", 45, "[4/7] No master grid — using 2km fallback…")
            trigger_score = trigger_score_2km
            grid_meta = grid_meta_2km
            high_res = False

        # ── [5] Susceptibility raster ─────────────────────────────────────────
        _log_job(job_id, "processing", 60, "[5/7] Finalizing susceptibility layer…")
        if susc_path and Path(susc_path).exists():
            susc_norm = aggregate_susceptibility(grid_meta, susc_path, high_res=high_res)
        else:
            susc_norm = np.full((grid_meta["nrows"], grid_meta["ncols"]), 0.5, dtype="float32")

        # ── [6] LULC ──────────────────────────────────────────────────────────
        _log_job(job_id, "processing", 75, "[6/7] Finalizing LULC layer…")
        lulc_path = get_lulc_path_from_db(settings.DATABASE_URL)
        if lulc_path and Path(lulc_path).exists():
            lulc_norm = aggregate_lulc(grid_meta, lulc_path, disaster_code, high_res=high_res)
        else:
            lulc_norm = np.full((grid_meta["nrows"], grid_meta["ncols"]), np.nan, dtype="float32")

        # ── [7] High-Res Combine → Final Classify ─────────────────────────────
        _log_job(job_id, "processing", 90, "[7/7] Computing pixel-wise risk & saving…")
        composite, risk = combine_and_classify(
            susc_norm, trigger_score, lulc_norm, disaster_code
        )
        comp_mean = float(np.nanmean(composite)) if np.isfinite(composite).any() else 0.0

        output_dir = _get_dynamic_output_dir(data_root, disaster_code, country, state, district)
        shp_path, geojson, class_stats = _save_to_shp_and_postgis(
            risk, grid_meta, disaster_code,
            region_id, country, state, district,
            target_date, engine, output_dir
        )

        # ── Log risk distribution ──────────────────────────────────────────────
        total = int((risk > 0).sum())
        dist_lines = []
        for cls in range(1, 6):
            n   = int((risk == cls).sum())
            pct = n / max(1, total) * 100
            dist_lines.append(f"    {CLASS_LABELS[cls]:10s}: {n:>6,} px  {pct:.1f}%")
        dprint("\n  Risk distribution:\n" + "\n".join(dist_lines))

        # ── Store metadata ─────────────────────────────────────────────────────
        _store_result_metadata(
            region_id, disaster_code, target_date,
            class_stats, geojson, comp_mean,
            trigger_method, physics_meta, shp_path
        )

        done_msg = (
            f"DYNAMIC RISK complete for {target_date}\n"
            f"  Resolution: {'High (Pixel Size)' if high_res else 'Low (2km)'}\n"
            f"  Method: {trigger_method}\n"
            f"  SHP Saved: {shp_path or 'n/a'}"
        )
        _log_job(job_id, "done", 100, done_msg)
        dprint(f"\n  Done.")

    except Exception as e:
        err_msg = f"FAILED: {e}\n{traceback.format_exc()}"
        print(err_msg, file=sys.stderr, flush=True)
        _log_job(job_id, "failed", 0, f"FAILED: {e}")


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/predict")
async def start_dynamic_prediction(
    req: DynamicPredictRequest,
    background_tasks: BackgroundTasks,
):
    _ensure_results_table()
    background_tasks.add_task(
        _run_dynamic_pipeline,
        req.job_id, req.region_id, req.disaster_code,
        req.state, req.country, req.district,
        req.target_date, req.antecedent_days,
    )
    return {
        "message": f"Dynamic {req.disaster_code} prediction started for {req.target_date}",
        "job_id": req.job_id,
    }


@router.get("/result/{region_id}/{disaster_code}/{target_date}")
async def get_dynamic_result(region_id: str, disaster_code: str, target_date: str):
    """
    Fetch dynamic risk result — reads from PostGIS (like susceptibility),
    supplements with metadata from dynamic_risk_results.
    """
    _ensure_results_table()
    engine = _get_engine()

    # 1. Get metadata row
    conn = _db_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, region_id, disaster_code, target_date,
                   class_stats, composite_mean, trigger_method,
                   physics_meta, shp_path, created_at
            FROM dynamic_risk_results
            WHERE region_id=%s AND disaster_code=%s AND target_date=%s
            ORDER BY created_at DESC LIMIT 1
        """, (region_id, disaster_code, target_date))
        meta = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not meta:
        raise HTTPException(
            404, detail=f"No dynamic result for {disaster_code}/{region_id}/{target_date}"
        )

    # 2. Read GeoJSON from PostGIS table (same way susceptibility backend reads)
    safe  = re.sub(r"[^a-z0-9_]", "_", disaster_code.lower())
    table = f"dynamic_risk_{safe}"
    geojson = {"type": "FeatureCollection", "features": []}
    
    # 2. Read from PostGIS table directly using ST_AsGeoJSON for speed
    try:
        conn = _db_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Using double percentage for PSQL date-string formatting if needed, but here simple %s works
        cur.execute(f"""
            SELECT class_id, risk_class, ST_AsGeoJSON(geom) as geojson
            FROM {table}
            WHERE region_id = %s
              AND target_date = %s
        """, (region_id, target_date))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
             return { **dict(meta), "risk_geojson": geojson, "warning": "No spatial features found in PostGIS." }
             
        features = []
        for r in rows:
            features.append({
                "type": "Feature",
                "properties": {
                    "class_id":  int(r["class_id"]),
                    "risk_class": r["risk_class"],
                    "color":      RISK_COLORS.get(int(r["class_id"]), "#ccc"),
                },
                "geometry": json.loads(r["geojson"]),
            })
        geojson["features"] = features
    except Exception as e:
        print(f"  [DynResult] PostGIS direct read warning: {e}")
        if 'conn' in locals() and conn: conn.close()

    return {
        **dict(meta),
        "risk_geojson": geojson,
    }


@router.get("/history/{region_id}/{disaster_code}")
async def get_dynamic_history(region_id: str, disaster_code: str, limit: int = 30):
    """List all past predictions (metadata only — no GeoJSON)."""
    _ensure_results_table()
    conn = _db_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, region_id, disaster_code, target_date,
                   class_stats, composite_mean, trigger_method, created_at
            FROM dynamic_risk_results
            WHERE region_id=%s AND disaster_code=%s
            ORDER BY target_date DESC LIMIT %s
        """, (region_id, disaster_code, limit))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return {"predictions": [dict(r) for r in rows]}


@router.get("/available-dates/{region_id}/{disaster_code}/{state}")
async def get_available_dates(region_id: str, disaster_code: str, state: str):
    """Return dates with weather data available for the given state."""
    _ensure_results_table()
    conn = _db_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT date FROM weather_data WHERE state=%s ORDER BY date DESC LIMIT 365",
            (state,)
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return {"available_dates": [str(r[0]) for r in rows], "count": len(rows)}
