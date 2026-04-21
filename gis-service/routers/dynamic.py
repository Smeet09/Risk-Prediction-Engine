"""
dynamic.py — FastAPI Router for Dynamic Risk Prediction
=========================================================
Exposes:
  POST /dynamic/predict
        Trigger dynamic risk computation for a region + disaster + date.
  GET  /dynamic/result/{region_id}/{disaster_code}/{target_date}
        Fetch stored dynamic result.
  GET  /dynamic/history/{region_id}/{disaster_code}
        List all past dynamic predictions for a region + disaster.

Full pipeline:
  1. Load 10-day weather window from weather_data DB table
  2. Build 2km weather grid
  3. Compute disaster-specific trigger score (landslide: TOPMODEL+FS, flood: SCS-CN)
  4. Load susceptibility raster (path from susceptibility_results table)
  5. Load LULC raster (path from manual_data_india table)
  6. Combine (susc + trigger + LULC) → composite → 5-class risk
  7. Vectorise → GeoJSON
  8. Store in dynamic_risk_results, update job
"""

import json
import traceback
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

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
)

router = APIRouter()


# ─── Request / Response schemas ───────────────────────────────────────────────

class DynamicPredictRequest(BaseModel):
    job_id:         str
    region_id:      str
    disaster_code:  str          # "landslide" | "flood"
    state:          str
    target_date:    str          # YYYY-MM-DD
    antecedent_days: int = 10


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _db_conn():
    return psycopg2.connect(settings.DATABASE_URL)


def _ensure_table():
    """Create dynamic_risk_results table if it does not exist."""
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
            cur = conn.cursor()
            cur.execute(ddl)
    finally:
        conn.close()


def _log_job(job_id: str, status: str, progress: int, msg: str):
    try:
        conn = _db_conn()
        with conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE jobs
                   SET status=%s, progress=%s,
                       log = log || E'\\n' || %s,
                       updated_at=NOW()
                   WHERE id=%s""",
                (status, progress, msg, job_id)
            )
    except Exception as e:
        print(f"  [JobLog] failed: {e}")
    finally:
        conn.close()


def _store_result(region_id, disaster_code, target_date,
                  class_stats, geojson, composite_mean,
                  trigger_method, physics_meta, tif_path=None):
    conn = _db_conn()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO dynamic_risk_results
                    (region_id, disaster_code, target_date,
                     class_stats, risk_geojson, composite_mean,
                     trigger_method, physics_meta, tif_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (region_id, disaster_code, target_date)
                DO UPDATE SET
                    class_stats    = EXCLUDED.class_stats,
                    risk_geojson   = EXCLUDED.risk_geojson,
                    composite_mean = EXCLUDED.composite_mean,
                    trigger_method = EXCLUDED.trigger_method,
                    physics_meta   = EXCLUDED.physics_meta,
                    tif_path       = EXCLUDED.tif_path,
                    created_at     = NOW()
            """, (
                region_id, disaster_code, target_date,
                json.dumps(class_stats),
                json.dumps(geojson),
                float(composite_mean) if composite_mean is not None else None,
                trigger_method,
                json.dumps(physics_meta) if physics_meta else None,
                tif_path,
            ))
    finally:
        conn.close()


# ─── Core pipeline ────────────────────────────────────────────────────────────

def _run_dynamic_pipeline(job_id: str, region_id: str, disaster_code: str,
                           state: str, target_date: str, antecedent_days: int):
    _ensure_table()
    print(f"\n{'='*60}")
    print(f"  DYNAMIC RISK — {disaster_code.upper()}  |  {state}  |  {target_date}")
    print(f"{'='*60}")

    try:
        # ── [1] Load weather from DB ───────────────────────────────────────────
        _log_job(job_id, "processing", 10, "[1/6] Loading weather data from DB…")
        weather_df = load_weather_from_db(
            settings.DATABASE_URL, state, target_date, antecedent_days
        )

        # ── [2] Build grid ─────────────────────────────────────────────────────
        _log_job(job_id, "processing", 20, "[2/6] Building 2km weather grid…")
        weather_df, grid_meta = build_grid(weather_df)

        # ── [3] Compute trigger score ──────────────────────────────────────────
        _log_job(job_id, "processing", 35, "[3/6] Computing trigger score…")

        if disaster_code == "landslide":
            from scripts.landslide_dynamic_db import compute_landslide_trigger
            # Try to find slope.tif from topographic_features dir
            slope_path = _find_slope_tif(region_id)
            trigger_score, trigger_method, physics_meta = compute_landslide_trigger(
                weather_df, grid_meta, slope_path=slope_path
            )
        elif disaster_code == "flood":
            from scripts.flood_dynamic_db import compute_flood_trigger
            trigger_score, trigger_method, physics_meta = compute_flood_trigger(
                weather_df, grid_meta
            )
        else:
            raise HTTPException(400, detail=f"Unsupported disaster_code: {disaster_code}")

        # ── [4] Susceptibility raster ──────────────────────────────────────────
        _log_job(job_id, "processing", 50, "[4/6] Aggregating susceptibility raster…")
        susc_path = get_susceptibility_path_from_db(
            settings.DATABASE_URL, region_id, disaster_code
        )
        # Susceptibility class TIF → use the *class* tif if available,
        # fall back to continuous one
        susc_class_path = _find_class_tif(region_id, disaster_code, susc_path)
        if susc_class_path and Path(susc_class_path).exists():
            susc_norm = aggregate_susceptibility(grid_meta, susc_class_path)
        elif susc_path and Path(susc_path).exists():
            susc_norm = aggregate_susceptibility(grid_meta, susc_path)
        else:
            _log_job(job_id, "processing", 50,
                     f"  WARNING: No susceptibility raster for {disaster_code}. "
                     "Generate susceptibility first.")
            import numpy as np
            susc_norm = np.full(
                (grid_meta["nrows"], grid_meta["ncols"]), 0.5, dtype="float32"
            )

        # ── [5] LULC raster ────────────────────────────────────────────────────
        _log_job(job_id, "processing", 62, "[5/6] Aggregating LULC…")
        lulc_path = get_lulc_path_from_db(settings.DATABASE_URL)
        import numpy as np
        if lulc_path and Path(lulc_path).exists():
            lulc_norm = aggregate_lulc(grid_meta, lulc_path, disaster_code)
        else:
            _log_job(job_id, "processing", 62,
                     "  WARNING: LULC not found — using 50/50 susc/trigger blend")
            lulc_norm = np.full(
                (grid_meta["nrows"], grid_meta["ncols"]), np.nan, dtype="float32"
            )

        # ── [6] Combine & classify ─────────────────────────────────────────────
        _log_job(job_id, "processing", 78, "[6/6] Combining layers & classifying…")
        composite, risk = combine_and_classify(
            susc_norm, trigger_score, lulc_norm, disaster_code
        )

        # ── Vectorise ──────────────────────────────────────────────────────────
        geojson     = raster_to_geojson(risk, grid_meta)
        class_stats = get_class_stats(risk)
        comp_mean   = float(np.nanmean(composite)) if np.isfinite(composite).any() else 0.0

        # Print risk distribution
        print(f"\n  Risk distribution ({target_date}):")
        from scripts.dynamic_core import RISK_LABELS
        total = int((risk > 0).sum())
        for cls in range(1, 6):
            n   = int((risk == cls).sum())
            pct = n / max(1, total) * 100
            bar = "█" * int(pct / 2)
            print(f"    {RISK_LABELS[cls]:12s}: {n:>6,} px  {pct:5.1f}%  {bar}")

        # ── Store result ───────────────────────────────────────────────────────
        _store_result(
            region_id, disaster_code, target_date,
            class_stats, geojson, comp_mean,
            trigger_method, physics_meta
        )

        _log_job(job_id, "done", 100,
                 f"✓ {disaster_code.upper()} dynamic risk computed for {target_date}\n"
                 f"  Method: {trigger_method}\n"
                 f"  Composite mean: {comp_mean:.3f}\n"
                 f"  Features: {len(geojson.get('features', []))}")

        print(f"\n  ✓ Done. Stored to dynamic_risk_results.")

    except Exception as e:
        err_msg = f"FAILED: {e}\n{traceback.format_exc()}"
        print(err_msg)
        _log_job(job_id, "failed", 0, f"FAILED: {e}")


def _find_slope_tif(region_id: str) -> str | None:
    """Look up slope.tif from topographic_features output_dir."""
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


def _find_class_tif(region_id: str, disaster_code: str,
                     fallback_tif: str | None) -> str | None:
    """
    Derive the class TIF path from the susceptibility continuous TIF path.
    Pattern: .../flood_susceptibility.tif → .../flood_class.tif
    """
    if not fallback_tif:
        return None
    p = Path(fallback_tif)
    cls_tif = p.parent / f"{disaster_code}_class.tif"
    return str(cls_tif) if cls_tif.exists() else fallback_tif


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/predict")
async def start_dynamic_prediction(
    req: DynamicPredictRequest,
    background_tasks: BackgroundTasks
):
    """
    Trigger dynamic risk prediction as a background job.
    Immediately returns 202. Poll /jobs/{job_id} for progress.
    """
    _ensure_table()
    background_tasks.add_task(
        _run_dynamic_pipeline,
        req.job_id,
        req.region_id,
        req.disaster_code,
        req.state,
        req.target_date,
        req.antecedent_days,
    )
    return {
        "message": f"Dynamic {req.disaster_code} prediction started for {req.target_date}",
        "job_id": req.job_id,
    }


@router.get("/result/{region_id}/{disaster_code}/{target_date}")
async def get_dynamic_result(region_id: str, disaster_code: str,
                              target_date: str):
    """Fetch stored dynamic result for a specific date."""
    _ensure_table()
    conn = _db_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, region_id, disaster_code, target_date,
                   class_stats, risk_geojson, composite_mean,
                   trigger_method, physics_meta, created_at
            FROM dynamic_risk_results
            WHERE region_id=%s AND disaster_code=%s AND target_date=%s
            ORDER BY created_at DESC LIMIT 1
        """, (region_id, disaster_code, target_date))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        raise HTTPException(
            404, detail=f"No dynamic result for {disaster_code} / {region_id} / {target_date}"
        )
    return dict(row)


@router.get("/history/{region_id}/{disaster_code}")
async def get_dynamic_history(region_id: str, disaster_code: str,
                               limit: int = 30):
    """List all past dynamic predictions for a region + disaster."""
    _ensure_table()
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
    """
    Return a list of dates that have weather data available for
    the given state. Used by the frontend date picker to show valid dates.
    """
    _ensure_table()
    conn = _db_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT date FROM weather_data
            WHERE state = %s
            ORDER BY date DESC
            LIMIT 365
        """, (state,))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    dates = [str(r[0]) for r in rows]
    return {"available_dates": dates, "count": len(dates)}
