import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import dem, era5, susceptibility, terrain, weather, india_layers, dynamic

app = FastAPI(
    title="Aether-Disaster GIS Microservice",
    version="2.0.0",
    description="FastAPI service for DEM processing, terrain classification, and disaster-wise susceptibility generation",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dem.router,           tags=["DEM & Terrain Pipeline"])
app.include_router(terrain.router,       prefix="/api/terrain", tags=["Terrain Classification"])
app.include_router(era5.router,          prefix="/api/era5", tags=["ERA5 Rainfall"])
app.include_router(susceptibility.router, prefix="/api/susceptibility", tags=["Susceptibility Generation"])
app.include_router(weather.router,          prefix="/weather", tags=["Weather Downloader"])
app.include_router(india_layers.router,     prefix="/api/india-layers", tags=["India-Wide Layers"])
app.include_router(dynamic.router,          prefix="/dynamic", tags=["Dynamic Risk Prediction"])


# Global state for tracking active background tasks
app.state.active_job_ids = set()

@app.get("/status/active-jobs")
def get_active_jobs():
    """Returns the list of job IDs currently being processed by this engine."""
    return {"active_jobs": list(app.state.active_job_ids)}


@app.get("/health")
def health():
    from config import settings
    return {
        "status":      "ok",
        "data_root":   str(settings.data_root_path),
        "coast_shp":   settings.coast_shp_path or "NOT FOUND",
        "dem_stub":    settings.DEM_STUB_MODE,
        "susc_stub":   settings.SUSC_STUB_MODE,
        "active_tasks": len(app.state.active_job_ids),
    }
