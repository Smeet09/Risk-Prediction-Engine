import os
import sys
import argparse
import psycopg2
import geopandas as gpd
import json

TARGET_CRS = "EPSG:4326"

# Column name guesses for the name field in each shapefile
NAME_COLS = {
    "state":    ["ST_NM", "STATE", "State_Name", "NAME", "state_name"],
    "district": ["DISTRICT", "Dist_Name", "District_N", "NAME", "district", "name11", "dtname"],
    "taluka":   ["TALUKA", "Taluka_Nam", "NAME", "taluka", "taluk", "tkname", "tehsil"],
    "village":  ["VILLAGE", "Village_Na", "NAME", "village", "vlname"],
}

# Parent reference columns (for linking hierarchy)
PARENT_COLS = {
    "district": {"state_name":    ["ST_NM", "STATE", "State_Name", "state", "stname"]},
    "taluka":   {"state_name":    ["ST_NM", "STATE", "state", "stname"],
                 "district_name": ["DISTRICT", "Dist_Name", "district", "name11", "dtname"]},
    "village":  {"state_name":    ["ST_NM", "STATE", "state", "stname"],
                 "district_name": ["DISTRICT", "Dist_Name", "district", "name11", "dtname"],
                 "taluka_name":   ["TALUKA", "Taluka_Nam", "taluka", "tkname", "tehsil"]},
}

def find_col(gdf, candidates):
    cols_lower = {c.lower(): c for c in gdf.columns}
    for c in candidates:
        if c in gdf.columns: return c
        if c.lower() in cols_lower: return cols_lower[c.lower()]
    return None

def import_level(conn, gdf, level, verbose=True):
    gdf = gdf.to_crs(TARGET_CRS)

    name_col = find_col(gdf, NAME_COLS[level])
    if not name_col:
        for c in gdf.columns:
            if c != "geometry" and gdf[c].dtype == object:
                name_col = c
                break

    rows = []
    for idx, row in gdf.iterrows():
        name = str(row.get(name_col, f"Unknown_{idx}")).strip() if name_col else f"Unknown_{idx}"
        state_name    = None
        district_name = None
        taluka_name   = None

        if level in PARENT_COLS:
            for field, candidates in PARENT_COLS[level].items():
                col = find_col(gdf, candidates)
                val = str(row[col]).strip() if col and col in row.index and row[col] else None
                if field == "state_name":    state_name = val
                if field == "district_name": district_name = val
                if field == "taluka_name":   taluka_name = val

        geom = row.geometry
        if geom is None or geom.is_empty: continue

        if geom.geom_type == "Polygon":
            from shapely.geometry import MultiPolygon
            geom = MultiPolygon([geom])

        rows.append((level, name, state_name, district_name, taluka_name,
                     geom.wkt, geom.centroid.wkt, geom.envelope.wkt, int(idx)))

    if not rows:
        print(f"[WARN] No valid structural rows generated")
        return 0

    table_name = f"{level}s_boundaries"
    BATCH = 500
    cur = conn.cursor()
    inserted = 0

    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            if level == "state":
                query = f"INSERT INTO {table_name} (name, geom, centroid, bbox, source_fid) VALUES (%s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"
                bd = [(b[1], b[5], b[6], b[7], b[8]) for b in batch]
            elif level == "district":
                query = f"INSERT INTO {table_name} (name, state_name, geom, centroid, bbox, source_fid) VALUES (%s, %s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"
                bd = [(b[1], b[2], b[5], b[6], b[7], b[8]) for b in batch]
            elif level == "taluka":
                query = f"INSERT INTO {table_name} (name, state_name, district_name, geom, centroid, bbox, source_fid) VALUES (%s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"
                bd = [(b[1], b[2], b[3], b[5], b[6], b[7], b[8]) for b in batch]
            else:
                query = f"INSERT INTO {table_name} (name, state_name, district_name, taluka_name, geom, centroid, bbox, source_fid) VALUES (%s, %s, %s, %s, ST_Multi(ST_GeomFromText(%s, 4326)), ST_GeomFromText(%s, 4326), ST_GeomFromText(%s, 4326), %s) ON CONFLICT DO NOTHING"
                bd = [(b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8]) for b in batch]

            cur.executemany(query, bd)
            conn.commit()
            inserted += len(batch)
            if verbose: print(f"    ... {inserted}/{len(rows)} features mapped")
        except Exception as e:
            conn.rollback()
            print(f"[ERROR] Batch {i}-{i+BATCH} conflict: {e}")

    cur.close()
    return inserted

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip-path", required=True)
    parser.add_argument("--level", required=True, choices=["state", "district", "taluka", "village"])
    parser.add_argument("--db-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    zip_path = os.path.abspath(args.zip_path)
    if not os.path.exists(zip_path):
        print(f"ERROR: File not found {zip_path}")
        sys.exit(1)

    print(f"\n[GIS Engine] Targeting level '{args.level.upper()}' from ZIP archive")
    
    conn = psycopg2.connect(args.db_url)
    
    table_name = f"{args.level}s_boundaries"
    if args.overwrite:
        print(f"[INFO] OVERWRITE requested. Clearing '{table_name}' table...")
        cur = conn.cursor()
        cur.execute(f"TRUNCATE {table_name} RESTART IDENTITY CASCADE;")
        conn.commit()
        cur.close()
        
    print("[INFO] Parsing SHP from archive via GeoPandas (this may take a moment based on size)...")
    try:
        gdf = gpd.read_file(f"zip://{zip_path}")
        print(f"[INFO] Analyzed. Geometries: {len(gdf)} | EPSG CRS: {gdf.crs}")
        n = import_level(conn, gdf, args.level)
        print(f"[SUCCESS] Committed {n} explicit records directly to '{table_name}'.")
    except Exception as e:
        print(f"[ERROR] Spatial ingestion failed natively: {e}")

    conn.close()

if __name__ == "__main__":
    main()
