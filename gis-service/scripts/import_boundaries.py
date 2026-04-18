"""
import_boundaries.py — One-Time India Boundary Importer
=========================================================
Reads India_BND shapefiles and populates the `india_boundaries` table.
Run once from the gis-service directory:

  python scripts/import_boundaries.py --dataset-dir ../Dataset --db-url <DATABASE_URL>

Levels imported (in order):
  State    → India_BND/State/State_BND.shp
  District → India_BND/District/District_BND.shp
  Taluka   → India_BND/Taluka/Taluka_BND.shp
  Village  → India_BND/Village/Village_BND.shp   (may be large, use --skip-village)
"""

import os
import sys
import argparse
import psycopg2
from psycopg2.extras import execute_values
import geopandas as gpd
from shapely.geometry import mapping
import json

TARGET_CRS = "EPSG:4326"

# Shapefile paths relative to Dataset folder
SHP_MAP = {
    "state":    ("India_BND", "State",    "State_BND.shp"),
    "district": ("India_BND", "District", "District_BND.shp"),
    "taluka":   ("India_BND", "Taluka",   "Taluka_BND.shp"),
    "village":  ("India_BND", "Village",  "Village_BND.shp"),
}

# Column name guesses for the name field in each shapefile
NAME_COLS = {
    "state":    ["ST_NM", "STATE", "State_Name", "NAME", "state_name"],
    "district": ["DISTRICT", "Dist_Name", "District_N", "NAME", "district"],
    "taluka":   ["TALUKA", "Taluka_Nam", "NAME", "taluka"],
    "village":  ["VILLAGE", "Village_Na", "NAME", "village"],
}

# Parent reference columns (for linking hierarchy)
PARENT_COLS = {
    "district": {"state_name":    ["ST_NM", "STATE", "State_Name", "state"]},
    "taluka":   {"state_name":    ["ST_NM", "STATE", "state"],
                 "district_name": ["DISTRICT", "Dist_Name", "district"]},
    "village":  {"state_name":    ["ST_NM", "STATE", "state"],
                 "district_name": ["DISTRICT", "Dist_Name", "district"],
                 "taluka_name":   ["TALUKA", "Taluka_Nam", "taluka"]},
}


def find_col(gdf, candidates):
    """Find first matching column from candidates list."""
    cols_lower = {c.lower(): c for c in gdf.columns}
    for c in candidates:
        if c in gdf.columns:
            return c
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


def import_level(conn, gdf, level, dataset_dir, verbose=True):
    gdf = gdf.to_crs(TARGET_CRS)

    # Find name column
    name_col = find_col(gdf, NAME_COLS[level])
    if not name_col:
        print(f"  [WARN] Could not find name column for {level}. Tried: {NAME_COLS[level]}")
        print(f"  Available columns: {list(gdf.columns)}")
        # Fall back to first non-geometry string column
        for c in gdf.columns:
            if c != "geometry" and gdf[c].dtype == object:
                name_col = c
                print(f"  Using fallback column: {name_col}")
                break

    rows = []
    for idx, row in gdf.iterrows():
        name = str(row.get(name_col, f"Unknown_{idx}")).strip() if name_col else f"Unknown_{idx}"

        # Parent references
        state_name    = None
        district_name = None
        taluka_name   = None

        if level in PARENT_COLS:
            for field, candidates in PARENT_COLS[level].items():
                col = find_col(gdf, candidates)
                val = str(row[col]).strip() if col and col in row.index and row[col] else None
                if field == "state_name":    state_name    = val
                if field == "district_name": district_name = val
                if field == "taluka_name":   taluka_name   = val

        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        # Convert to MultiPolygon if needed
        if geom.geom_type == "Polygon":
            from shapely.geometry import MultiPolygon
            geom = MultiPolygon([geom])

        geom_wkt     = geom.wkt
        centroid_wkt = geom.centroid.wkt
        bbox_wkt     = geom.envelope.wkt
        fid          = int(idx)

        rows.append((level, name, state_name, district_name, taluka_name,
                     geom_wkt, centroid_wkt, bbox_wkt, fid))

    if not rows:
        print(f"  [WARN] No valid rows for level '{level}'")
        return 0

    print(f"  Inserting {len(rows)} {level} records ...")

    # Map the level to the exact table name
    table_name = f"{level}s_boundaries"

    BATCH = 500
    cur = conn.cursor()
    inserted = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            # Different tables have different columns constraints. But we can insert into
            # columns explicitly.
            # states: name, geom, centroid, bbox, source_fid
            # districts: name, state_name ... 
            # talukas: name, district_name, state_name ...
            # villages: name, taluka_name, district_name, state_name ...
            
            if level == "state":
                query = f"""INSERT INTO {table_name} (name, geom, centroid, bbox, source_fid) 
                            VALUES (%s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"""
                batch_data = [(b[1], b[5], b[6], b[7], b[8]) for b in batch]
                
            elif level == "district":
                query = f"""INSERT INTO {table_name} (name, state_name, geom, centroid, bbox, source_fid) 
                            VALUES (%s, %s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"""
                batch_data = [(b[1], b[2], b[5], b[6], b[7], b[8]) for b in batch]
                
            elif level == "taluka":
                query = f"""INSERT INTO {table_name} (name, state_name, district_name, geom, centroid, bbox, source_fid) 
                            VALUES (%s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"""
                batch_data = [(b[1], b[2], b[3], b[5], b[6], b[7], b[8]) for b in batch]
                
            else: # village
                query = f"""INSERT INTO {table_name} (name, state_name, district_name, taluka_name, geom, centroid, bbox, source_fid) 
                            VALUES (%s, %s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"""
                batch_data = [(b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8]) for b in batch]

            cur.executemany(query, batch_data)
            conn.commit()
            inserted += len(batch)
            if verbose:
                print(f"    ... {inserted}/{len(rows)}")
        except Exception as e:
            conn.rollback()
            print(f"  [ERROR] Batch {i}-{i+BATCH}: {e}")

    cur.close()
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Import India BND shapefiles to PostGIS")
    parser.add_argument("--dataset-dir", default="../Dataset",
                        help="Path to Dataset folder containing India_BND/")
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", ""),
                        help="PostgreSQL connection URL")
    parser.add_argument("--skip-village", action="store_true",
                        help="Skip village level import (large dataset)")
    parser.add_argument("--levels", nargs="+",
                        choices=["state", "district", "taluka", "village"],
                        default=["state", "district", "taluka", "village"],
                        help="Which levels to import")
    args = parser.parse_args()

    if not args.db_url:
        print("ERROR: --db-url required (or set DATABASE_URL env var)")
        sys.exit(1)

    dataset_dir = os.path.abspath(args.dataset_dir)
    print(f"\n{'='*60}")
    print("  INDIA BOUNDARY IMPORTER")
    print(f"  Dataset dir: {dataset_dir}")
    print(f"{'='*60}\n")

    conn = psycopg2.connect(args.db_url)

    levels = args.levels
    if args.skip_village and "village" in levels:
        levels = [l for l in levels if l != "village"]
        print("  Skipping village level (--skip-village)")

    total_inserted = 0
    for level in levels:
        shp_parts = SHP_MAP[level]
        shp_path = os.path.join(dataset_dir, *shp_parts)

        if not os.path.exists(shp_path):
            print(f"[SKIP] {level}: {shp_path} not found")
            continue

        print(f"[{level.upper()}] Reading {shp_path} ...")
        try:
            gdf = gpd.read_file(shp_path)
            print(f"  Rows: {len(gdf)}  |  CRS: {gdf.crs}  |  Cols: {list(gdf.columns)}")
            n = import_level(conn, gdf, level, dataset_dir)
            print(f"  [SUCCESS] Inserted {n} {level} boundaries")
            total_inserted += n
        except Exception as e:
            print(f"  [ERROR] Failed to import {level}: {e}")

    conn.close()
    print(f"\n{'='*60}")
    print(f"  DONE — Total records inserted: {total_inserted}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
