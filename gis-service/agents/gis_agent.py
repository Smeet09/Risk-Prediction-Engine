import asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import psycopg2
import requests
from config import settings
from routers.dynamic import _run_dynamic_pipeline

router = APIRouter()

class GISDailyRunRequest(BaseModel):
    job_id: str
    target_date: str | None = None # YYYY-MM-DD. Defaults to today's date

def remote_log(run_id, agent_name, agent_label, status, progress, step, error=None):
    try:
        url = f"{settings.BACKEND_URL}/api/agents/logs"
        payload = {
            "run_id": run_id,
            "agent_name": agent_name,
            "agent_label": agent_label,
            "status": status,
            "progress_pct": progress,
            "current_step": step,
            "error": error
        }
        requests.post(url, json=payload, timeout=5)
    except: pass

def get_active_regions():
    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # MUST ONLY pull regions that have Susceptibility Base Maps 100% computed!
    cur.execute("""SELECT r.id, r.country, r.state, r.district 
                 FROM regions r 
                 JOIN data_inventory d ON r.id = d.region_id 
                 WHERE d.susceptibility_ready = true""")
    regions = cur.fetchall()
    cur.close()
    conn.close()
    return regions

# ---------------------------------------------------------
# Daily Dynamic Risk Mapping Task (Synchronous blocking)
# ---------------------------------------------------------
def get_active_disasters():
    conn = psycopg2.connect(settings.DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT code FROM disaster_types WHERE is_active = true")
    disasters = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return disasters

def run_daily_gis(req: GISDailyRunRequest):
    """
    Renders risk for all active regions and disasters for a specific day.
    """
    target = req.target_date
    if not target:
        from datetime import timedelta
        # Match the ERA5 5-day lag so weather data is guaranteed to exist
        target = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        
    regions = get_active_regions()
    disasters = get_active_disasters()
    
    if not regions or not disasters:
        remote_log(req.job_id, "gis", "Skipped", "success", 100, f"No regions or disasters are ready for processing on {target}.")
        return

    total_tasks = len(regions) * len(disasters)
    task_count = 0
    
    # Iterate safely with massive fault-tolerance
    for r in regions:
        region_id = str(r["id"])
        
        for disaster in disasters:
            task_count += 1
            msg = f"Task {task_count}/{total_tasks}: Mapping {r['state']} ({disaster}) for {target}..."
            remote_log(req.job_id, "gis", "Generating Maps", "processing", int((task_count/total_tasks)*100), msg)
            
            # Self-Solving Retry Logic
            max_retries = 3
            success = False
            
            for attempt in range(1, max_retries + 1):
                try:
                    _run_dynamic_pipeline(
                        job_id=req.job_id,
                        region_id=region_id,
                        disaster_code=disaster,
                        state=r["state"],
                        country=r["country"],
                        district=r["district"],
                        target_date=target,
                        antecedent_days=10
                    )
                    success = True
                    break 
                except Exception as e:
                    import time
                    print(f"[GIS Agent] Attempt {attempt}/{max_retries} failed for {r['state']} {disaster}: {str(e)}")
                    time.sleep(10) 
                    
            if not success:
                # Fatal Error - Hard halt
                fatal_msg = f"FATAL ERROR: GIS Mapping failed for {region_id} ({disaster}) after {max_retries} attempts."
                raise Exception(fatal_msg) # Throws 500 back to n8n

# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------
@router.post("/daily-run")
async def trigger_daily_gis_run(req: GISDailyRunRequest):
    """
    Trigger daily GIS risk mapping. SYNCHRONOUS.
    Blocks the response until totally complete so n8n Orchestrator knows to wait.
    """
    try:
        # Run blocking code in a thread to not block the ASGI loop
        await asyncio.to_thread(run_daily_gis, req)
        return {"message": "Daily GIS mapping complete.", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
