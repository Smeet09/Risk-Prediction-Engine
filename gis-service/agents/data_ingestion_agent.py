import asyncio
from datetime import datetime, timedelta
import threading
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import psycopg2
from config import settings
from scripts.weather_downloader import WeatherDownloader

router = APIRouter()

class BackfillRequest(BaseModel):
    years_back: int = 10
    country: str = "India"
    job_id: str

class DailySyncRequest(BaseModel):
    country: str = "India"
    job_id: str
    target_date: str | None = None # YYYY-MM-DD. Defaults to yesterday

# Helper to get all active states in DB
def get_active_states(country: str):
    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT state FROM regions WHERE country = %s", (country,))
    states = [row[0] for row in cur.fetchall()]
    conn.close()
    return states

import time

# ---------------------------------------------------------
# Daily Sync Task
# ---------------------------------------------------------
def run_daily_sync(req: DailySyncRequest):
    """
    Downloads weather for all active regions for exactly ONE day.
    """
    target = req.target_date
    if not target:
        target = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        
    year, month, day = map(int, target.split("-"))
    states = get_active_states(req.country)
    downloader = WeatherDownloader()
    
    # Iterate safely with massive fault-tolerance
    for state in states:
        msg = f"Syncing {state} for {year}-{month}..."
        downloader._log(req.job_id, "processing", 10, msg)
        downloader._remote_log(req.job_id, "data_ingestion", "Fetching Weather", "processing", 10, msg)
        
        # Self-Solving Retry Logic
        max_retries = 3
        success = False
        
        for attempt in range(1, max_retries + 1):
            try:
                downloader.run(req.country, state, year, month, req.job_id)
                success = True
                break # It worked, exit the retry loop
            except Exception as e:
                err_msg = f"Attempt {attempt}/{max_retries} failed for {state}: {str(e)}"
                downloader._log(req.job_id, "processing", 10, err_msg)
                downloader._remote_log(req.job_id, "data_ingestion", "Retrying", "processing", 10, err_msg)
                time.sleep(60) 
                
        if not success:
            # Fatal Error - Hard halt. Do NOT skip or allow partial data!
            fatal_msg = f"FATAL ERROR: Failed to fetch data for {state} after {max_retries} attempts. System halting to prevent corrupted maps."
            downloader._log(req.job_id, "failed", 0, fatal_msg)
            raise Exception(fatal_msg) # Throws 500 back to n8n to break the chain

    downloader._log(req.job_id, "done", 100, f"Daily Sync complete for {len(states)} states for month {year}-{month}.")

# ---------------------------------------------------------
# Massive 10-Year Backfill Task (Left asynchronous as it takes days)
# ---------------------------------------------------------
def run_10_year_backfill(req: BackfillRequest):
    states = get_active_states(req.country)
    current_year = datetime.now().year
    current_month = datetime.now().month
    downloader = WeatherDownloader()
    downloader._log(req.job_id, "processing", 2, f"Starting {req.years_back}-year backfill for {len(states)} states.")

    total_months = req.years_back * 12
    completed = 0
    for state in states:
        for y in range(current_year, current_year - req.years_back, -1):
            start_m = current_month if y == current_year else 12
            for m in range(start_m, 0, -1):
                try:
                    downloader._log(req.job_id, "processing", int(2 + (completed/(len(states)*total_months)*90)), 
                                  f"Backfilling {state} : {y}-{m:02d}...")
                    downloader.run(req.country, state, y, m, req.job_id)
                except Exception as e:
                    downloader._log(req.job_id, "processing", int(2 + (completed/(len(states)*total_months)*90)), 
                                  f"Failed {state} : {y}-{m:02d} : {str(e)}")
                completed += 1
    downloader._log(req.job_id, "done", 100, "Historical 10-year deep backfill complete.")

# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------
@router.post("/daily-sync")
async def trigger_daily_sync(req: DailySyncRequest):
    """
    Trigger daily weather sync. SYNCHRONOUS.
    Blocks the response until totally complete so n8n Orchestrator knows to wait.
    """
    try:
        run_daily_sync(req)
        return {"message": "Daily weather sync complete.", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/backfill-historical")
async def trigger_backfill(req: BackfillRequest, background_tasks: BackgroundTasks):
    """Trigger a very long running 10-year backfill. ASYNCHRONOUS."""
    background_tasks.add_task(run_10_year_backfill, req)
    return {"message": f"{req.years_back}-year backfill started in background...", "job_id": req.job_id}
