import os, re, time, json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import settings
import random  # used only in stub

router = APIRouter()

class Era5Request(BaseModel):
    region_id: str
    date: str          # YYYY-MM-DD
    bbox: dict | None = None   # GeoJSON bbox polygon
    country: str
    state: str | None = None
    district: str | None = None

def _safe(s: str | None) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s or "_none")

@router.post("/fetch-era5")
async def fetch_era5(req: Era5Request):
    """
    ERA5 Hourly Rainfall Fetch.
    ----------------------------
    STUB MODE: Returns 24 hours of synthetic rainfall data.
    PRODUCTION: Uses cdsapi to download ERA5-Land hourly precipitation.

    Requires:
        CDS_KEY in .env (Copernicus account key)
        pip install cdsapi
    """
    cache_dir = (
        settings.data_root_path
        / _safe(req.country)
        / _safe(req.state)
        / _safe(req.district)
        / "rainfall_cache"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{req.date}.json"

    # Return cached result if available
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
        return {"timeseries": data, "source": "cache"}

    if settings.DEM_STUB_MODE:
        # ---- STUB: Synthetic 24-hour rainfall ----
        timeseries = []
        base = random.uniform(0, 5)
        for hour in range(24):
            # Simulate a rainfall peak around midday
            rain = max(0, base + random.gauss(0, 1) + (5 if 10 <= hour <= 16 else 0))
            timeseries.append({"hour": hour, "rainfall_mm": round(rain, 3)})

        cache_file.write_text(json.dumps(timeseries))
        return {"timeseries": timeseries, "source": "stub"}

    # ---- PRODUCTION: Real ERA5 fetch ----
    try:
        import cdsapi
        import zipfile

        uid, key = settings.CDS_KEY.split(":") if ":" in settings.CDS_KEY else ("", settings.CDS_KEY)

        # Compute bounding box for ERA5 request
        area = [90, -180, -90, 180]   # default: global
        if req.bbox and req.bbox.get("coordinates"):
            coords = req.bbox["coordinates"][0]
            lats = [c[1] for c in coords]
            lons = [c[0] for c in coords]
            # ERA5 area format: [North, West, South, East]
            area = [max(lats) + 0.1, min(lons) - 0.1, min(lats) - 0.1, max(lons) + 0.1]

        year, month, day = req.date.split("-")

        c = cdsapi.Client(url=settings.CDS_URL, key=f"{uid}:{key}", quiet=True)
        nc_path = str(cache_dir / f"{req.date}.nc")

        c.retrieve(
            "reanalysis-era5-land",
            {
                "product_type": "reanalysis",
                "variable": ["total_precipitation"],
                "year": year,
                "month": month,
                "day": day,
                "time": [f"{h:02d}:00" for h in range(24)],
                "area": area,
                "data_format": "netcdf",
            },
            nc_path,
        )

        # Parse NetCDF → hourly mm
        import numpy as np
        try:
            import netCDF4 as nc
            ds = nc.Dataset(nc_path)
            tp = ds.variables["tp"][:]  # shape: (24, lat, lon)
            timeseries = []
            for h in range(min(24, tp.shape[0])):
                spatial_mean = float(np.nanmean(tp[h])) * 1000  # m → mm
                timeseries.append({"hour": h, "rainfall_mm": round(max(0, spatial_mean), 3)})
            ds.close()
        except ImportError:
            raise HTTPException(500, detail="netCDF4 not installed. Run: pip install netCDF4")

        cache_file.write_text(json.dumps(timeseries))
        return {"timeseries": timeseries, "source": "era5"}

    except Exception as e:
        raise HTTPException(500, detail=f"ERA5 fetch failed: {str(e)}")
