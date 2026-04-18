import json
import geopandas as gpd
from sqlalchemy import text
from geoalchemy2 import Geometry, WKTElement
import os
from pathlib import Path

class SpatialDB:
    """
    Utility for loading Shapefiles (.shp) into the PostGIS regions table.
    Ensures high-performance spatial queries by storing boundaries in the database.
    """
    def __init__(self, db_url: str):
        from sqlalchemy import create_engine
        self.engine = create_engine(db_url)

    def import_boundary(self, shp_path: Path, country: str, state: str = None, district: str = None):
        """
        Parses a .shp file, reprojects to EPSG:4326, and updates the regions record.
        """
        if not shp_path.exists():
            return f"Error: Shapefile not found at {shp_path}"

        # Load and reproject
        gdf = gpd.read_file(str(shp_path))
        if gdf.crs != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        # Combine all parts into a single MultiPolygon
        combined_geom = gdf.unary_union
        wkt = combined_geom.wkt
        
        # Update regional record in PostGIS
        query = text("""
            UPDATE regions 
            SET geom = ST_GeomFromText(:wkt, 4326),
                centroid = ST_Centroid(ST_GeomFromText(:wkt, 4326)),
                bbox = ST_Envelope(ST_GeomFromText(:wkt, 4326))
            WHERE country = :country 
              AND (state = :state OR (state IS NULL AND :state IS NULL))
              AND (district = :district OR (district IS NULL AND :district IS NULL))
        """)
        
        with self.engine.begin() as conn:
            conn.execute(query, {
                "wkt": wkt,
                "country": country,
                "state": state,
                "district": district
            })
            
        return f"Successfully imported boundary for {country}/{state or ''}/{district or ''}"
    def get_boundary(self, country: str, state: str = None, district: str = None):
        """Fetches the region's geometry as a GeoJSON dictionary."""
        query = text("""
            SELECT ST_AsGeoJSON(geom) as geojson 
            FROM regions 
            WHERE country = :country 
              AND (state = :state OR (state IS NULL AND :state IS NULL))
              AND (district = :district OR (district IS NULL AND :district IS NULL))
        """)
        
        with self.engine.connect() as conn:
            result = conn.execute(query, {
                "country": country,
                "state": state,
                "district": district
            }).fetchone()
            
            if result and result[0]:
                return json.loads(result[0])
        return None
