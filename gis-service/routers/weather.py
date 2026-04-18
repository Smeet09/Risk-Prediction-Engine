import asyncio
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from scripts.weather_downloader import WeatherDownloader

router = APIRouter()

class WeatherDownloadRequest(BaseModel):
    country: str
    state: str
    year: int
    month: int
    job_id: str

@router.post("/download")
async def start_weather_download(req: WeatherDownloadRequest, background_tasks: BackgroundTasks):
    downloader = WeatherDownloader()
    
    # Adding to background tasks so API returns 202 immediately
    background_tasks.add_task(
        downloader.run,
        req.country,
        req.state,
        req.year,
        req.month,
        req.job_id
    )
    
    return {"message": "Weather download process started in background."}
