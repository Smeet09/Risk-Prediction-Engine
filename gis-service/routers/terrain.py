"""
terrain.py — Terrain Classification Router
============================================
Endpoints related to terrain class results and re-classification.
"""
import sys
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import settings

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from terrain_classifier import classify as terrain_classify, CLASSES, CLASS_DESCRIPTION

router = APIRouter()


class ReclassifyRequest(BaseModel):
    features_dir: str   # absolute path to dem_features/
    region_id:    str


@router.post("/reclassify")
async def reclassify(req: ReclassifyRequest):
    """Re-run terrain classification on an existing dem_features directory."""
    if not Path(req.features_dir).is_dir():
        raise HTTPException(404, detail=f"features_dir not found: {req.features_dir}")
    try:
        result = terrain_classify(
            features_dir=req.features_dir,
            coast_shp_path=settings.coast_shp_path,
        )
        return {"status": "done", "terrain": result}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.get("/terrain-legend")
async def terrain_legend():
    """Return the 12 terrain class definitions."""
    return {
        "classes": [
            {
                "id":          cid,
                "name":        name,
                "description": CLASS_DESCRIPTION.get(cid, ""),
            }
            for cid, name in CLASSES.items()
        ]
    }


@router.get("/boundary-status")
async def boundary_status():
    """Check how many boundary records exist per level (for import status)."""
    try:
        import asyncpg
        conn = await asyncpg.connect(settings.DATABASE_URL)
        rows = await conn.fetch(
            "SELECT level, COUNT(*) as cnt FROM india_boundaries GROUP BY level ORDER BY level"
        )
        await conn.close()
        return {
            "counts": {r["level"]: r["cnt"] for r in rows},
            "total":  sum(r["cnt"] for r in rows),
        }
    except Exception as e:
        return {"counts": {}, "total": 0, "error": str(e)}
