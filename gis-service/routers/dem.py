import re, time
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from config import settings
from utils.topo_processor import TopoProcessor

# Import terrain classifier (add scripts dir to path)
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from terrain_classifier import classify as terrain_classify

router = APIRouter(prefix="/api/dem")


class DemRequest(BaseModel):
    job_id:    str
    region_id: str
    file_path: str
    country:   str
    state:     str | None = None
    district:  str | None = None
    stage:     str | None = "all" # topo | terrain | all


def _safe(s: str | None) -> str:
    if not s or s.lower() == "none" or str(s).strip() == "": return "_state_level"
    return re.sub(r"[^a-zA-Z0-9_-]", "_", s)


@router.post("/process-dem")
async def process_dem(req: DemRequest, request: Request):
    """
    Modular DEM pipeline with real-time active task tracking.
    """
    # Register job as active
    request.app.state.active_job_ids.add(req.job_id)

    try:
        out_dir = (
            settings.data_root_path
            / _safe(req.country)
            / _safe(req.state)
            / _safe(req.district)
            / "dem_features"
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        topo_log = ""
        terrain_result = None

        # --- PHASE 1: Topographic Extraction ---
        if req.stage in ["all", "topo"]:
            try:
                print(f"[GIS] Stage 1: Starting Topographic Extraction for {req.state}...")
                processor = TopoProcessor(out_dir)
                topo_log  = processor.process(Path(req.file_path))
            except Exception as e:
                raise HTTPException(500, detail=f"Topographic extraction failed: {e}")

        # --- PHASE 2: Terrain Classification ---
        if req.stage in ["all", "terrain"]:
            try:
                print(f"[GIS] Stage 2: Starting Terrain Classification for {req.state}...")
                terrain_result = terrain_classify(
                    features_dir=str(out_dir),
                    coast_shp_path=settings.coast_shp_path,
                )
            except Exception as e:
                raise HTTPException(500, detail=f"Terrain classification failed: {e}")

        # Build final combined log if all stages run
        full_log = ""
        if topo_log:
            full_log += f"Stage 1 — Topographic extraction:\n{topo_log}\n\n"
        if terrain_result:
            full_log += (
                f"Stage 2 — Terrain classification:\n"
                f"  Dominant class: {terrain_result['dominant_class']} "
                f"({terrain_result['dominant_name']})\n"
                f"  Output TIF: {terrain_result['tif_path']}\n"
                f"  Output SHP: {terrain_result['shp_path']}"
            )

        return {
            "status": "done",
            "output_dir": str(out_dir),
            "log": full_log,
            "stage": req.stage,
            "terrain": terrain_result,
        }
    finally:
        # Unregister job when finished (success or fail)
        request.app.state.active_job_ids.discard(req.job_id)


@router.post("/process-exposure")
async def process_exposure(req: DemRequest):
    """Exposure & Vulnerability data processing."""
    out_dir = (
        settings.data_root_path
        / _safe(req.country)
        / _safe(req.state)
        / _safe(req.district)
        / "exposure_vulnerability"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    if settings.DEM_STUB_MODE:
        time.sleep(1)
        stub_log = (
            f"[STUB] Exposure/Vulnerability processed\n"
            f"Output: {out_dir}\n"
            "Validated: roads.shp, buildings.shp — projection OK"
        )
        (out_dir / "exposure_stub.txt").write_text(stub_log)
        return {"status": "done", "output_dir": str(out_dir), "log": stub_log}

    raise HTTPException(501, detail="Production exposure script not yet configured")


@router.post("/process-manual")
async def process_manual(req: DemRequest):
    """India-wide manual data processing (stores to global manual_data dir)."""
    out_dir = settings.data_root_path / "manual_data_india"
    out_dir.mkdir(parents=True, exist_ok=True)

    if settings.DEM_STUB_MODE:
        time.sleep(1)
        stub_log = (
            f"[STUB] Manual data normalized\n"
            f"Output: {out_dir}\n"
            "Datasets: soil.shp, lulc.shp, river.shp, fault.shp"
        )
        (out_dir / "manual_stub.txt").write_text(stub_log)
        return {"status": "done", "output_dir": str(out_dir), "log": stub_log}

    raise HTTPException(501, detail="Production manual script not yet configured")
