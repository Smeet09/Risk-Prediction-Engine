import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings

# Resolve dataset directory relative to this file's location
_GIS_SERVICE_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT    = _GIS_SERVICE_DIR.parent

class Settings(BaseSettings):
    DATA_ROOT:    str = str(_PROJECT_ROOT / "database")
    DATABASE_URL: str = "postgresql://aether:aether_secret@localhost:5432/aether_disaster"
    CDS_URL:      str = "https://cds.climate.copernicus.eu/api"
    CDS_KEY:      str = "c8969bb3-79bf-4a62-951e-c0e2d6c1788d"
    HOST:         str = "0.0.0.0"
    PORT:         int = 8000
    DEM_STUB_MODE:  bool = True
    SUSC_STUB_MODE: bool = False   # ← Production: real scripts are now used

    # Dataset paths (resolved at startup)
    DATASET_DIR:   str = str(_PROJECT_ROOT / "Dataset")
    COAST_SHP:     str = str(_PROJECT_ROOT / "Dataset" / "Coastline_All" / "Coastline_All" / "Coastline_All.shp")
    INDIA_BND_DIR: str = str(_PROJECT_ROOT / "Dataset" / "India_BND")

    @property
    def data_root_path(self) -> Path:
        return Path(self.DATA_ROOT).resolve()

    @property
    def coast_shp_path(self) -> str:
        """Return coastline SHP path from env or default."""
        p = Path(self.COAST_SHP)
        return str(p) if p.exists() else ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
