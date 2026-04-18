const express = require("express");
const router = express.Router();
const pool = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const fs = require("fs");
const path = require("path");

// GET /api/regions — full region tree as { countries: [...] }
router.get("/", protect, async (_req, res) => {
  const { rows } = await pool.query(
    `SELECT id, country, state, district,
       ST_AsGeoJSON(centroid)::json AS centroid,
       ST_AsGeoJSON(bbox)::json AS bbox
     FROM regions ORDER BY country, state, district`
  );

  // Build nested tree: country -> state -> district[]
  const tree = {};
  rows.forEach((r) => {
    if (!tree[r.country]) tree[r.country] = {};
    const state = r.state || "__none__";
    if (!tree[r.country][state]) tree[r.country][state] = [];
    tree[r.country][state].push({
      id: r.id,
      district: r.district,
      centroid: r.centroid,
      bbox: r.bbox,
    });
  });

  const countries = Object.entries(tree).map(([country, states]) => ({
    country,
    states: Object.entries(states).map(([state, districts]) => ({
      state: state === "__none__" ? null : state,
      districts,
    })),
  }));

  res.json({ countries });
});

const { getGlobalManualReadiness } = require("./manual");

// GET /api/regions/flat — flat array of all regions with detailed pipeline status
router.get("/flat", protect, async (_req, res) => {
  const globalManualReady = await getGlobalManualReadiness();
  
  const { rows } = await pool.query(
    `SELECT r.id, r.country, r.state, r.district,
       ST_AsGeoJSON(r.centroid)::json AS centroid,
       ST_AsGeoJSON(r.bbox)::json AS bbox,
       di.dem_ready, di.terrain_ready, di.manual_india_ready, di.susceptibility_ready,
       (tf.region_id IS NOT NULL) AS topo_ready,
       tc.dominant_name AS dominant_terrain
     FROM regions r
     LEFT JOIN data_inventory di ON di.region_id = r.id
     LEFT JOIN terrain_classifications tc ON tc.region_id = r.id
     LEFT JOIN topographic_features tf ON tf.region_id = r.id
     ORDER BY r.country, r.state, r.district`
  );

  // Overlay dynamic global status
  const regions = rows.map(r => ({
    ...r,
    manual_india_ready: globalManualReady
  }));

  res.json({ regions });
});

// GET /api/regions/complete — only regions where all data is ready
router.get("/complete", protect, async (_req, res) => {
  const globalManualReady = await getGlobalManualReadiness();
  if (!globalManualReady) return res.json({ regions: [] });

  const { rows } = await pool.query(
    `SELECT r.id, r.country, r.state, r.district,
       ST_AsGeoJSON(r.centroid)::json AS centroid
     FROM regions r
     JOIN data_inventory di ON di.region_id = r.id
     WHERE di.dem_ready = TRUE
       AND di.terrain_ready = TRUE
     ORDER BY r.country, r.state, r.district`
  );
  res.json({ regions: rows });
});

// POST /api/regions — create a new region (admin)
router.post("/", protect, async (req, res) => {
  const { country, state, district, centroid_lng, centroid_lat, bbox } = req.body;
  if (!country) return res.status(400).json({ error: "country is required" });

  const centroidWKT = centroid_lng != null && centroid_lat != null
    ? `ST_SetSRID(ST_MakePoint(${centroid_lng}, ${centroid_lat}), 4326)`
    : "NULL";

  const { rows } = await pool.query(
    `INSERT INTO regions (country, state, district, centroid)
     VALUES ($1, $2, $3, ${centroidWKT})
     ON CONFLICT (country, state, district) DO NOTHING
     RETURNING id`,
    [country, state || null, district || null]
  );

  if (rows.length === 0) {
    return res.status(409).json({ error: "Region already exists" });
  }

  // Create inventory entry
  // Set manual_india_ready to TRUE initially if India-wide datasets are already available
  const { rows: manualCount } = await pool.query("SELECT COUNT(*) FROM manual_data_india");
  const isIndiaReady = parseInt(manualCount[0].count) >= 4;

  await pool.query(
    `INSERT INTO data_inventory (region_id, manual_india_ready) VALUES ($1, $2) ON CONFLICT DO NOTHING`,
    [rows[0].id, isIndiaReady]
  );

  res.status(201).json({ region: rows[0] });
});

// DELETE /api/regions/:id/data — manually purge all physical data and record for a region
router.delete("/:id/data", protect, adminOnly, async (req, res) => {
  const { id } = req.params;
  const DATA_ROOT = req.app.locals.DATA_ROOT;

  function _safe(s) {
    return (s || "_none").replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  try {
    // 1. Get region details
    const { rows: reg } = await pool.query(`SELECT * FROM regions WHERE id=$1`, [id]);
    if (reg.length === 0) return res.status(404).json({ error: "Region not found" });
    const r = reg[0];

    // 2. Resolve and delete physical path
    const destDir = path.join(
      DATA_ROOT,
      _safe(r.country),
      _safe(r.state),
      r.district ? _safe(r.district) : "_state_level",
      "dem_features"
    );

    if (fs.existsSync(destDir)) {
      fs.rmSync(destDir, { recursive: true, force: true });
    }

    // 3. Reset database records
    await pool.query(
      `UPDATE data_inventory 
       SET dem_ready=FALSE, terrain_ready=FALSE, manual_india_ready=FALSE, susceptibility_ready=FALSE, updated_at=NOW()
       WHERE region_id=$1`,
      [id]
    );
    await pool.query(`DELETE FROM dem_uploads WHERE region_id=$1`, [id]);
    await pool.query(`DELETE FROM topographic_features WHERE region_id=$1`, [id]);
    await pool.query(`DELETE FROM terrain_classifications WHERE region_id=$1`, [id]);
    await pool.query(`DELETE FROM susceptibility_results WHERE region_id=$1`, [id]);

    res.json({ message: `Purged all data for ${r.district || r.state || r.country}` });
  } catch (err) {
    res.status(500).json({ error: `Purge failed: ${err.message}` });
  }
});

// GET /api/regions/:id/terrain — terrain classification result for a region
// Used by SusceptibilityMapping Step 3 to prefill actual terrain weights
router.get("/:id/terrain", protect, async (req, res) => {
  const { id } = req.params;
  const { rows } = await pool.query(
    `SELECT tc.class_stats, tc.dominant_class, tc.dominant_name, tc.classified_at,
            r.country, r.state, r.district
     FROM terrain_classifications tc
     JOIN regions r ON r.id = tc.region_id
     WHERE tc.region_id = $1`,
    [id]
  );
  if (!rows.length) {
    return res.status(404).json({ error: "No terrain classification found for this region. Process a DEM first." });
  }
  res.json({
    region_id:      id,
    class_stats:    rows[0].class_stats || {},
    dominant_class: rows[0].dominant_class,
    dominant_name:  rows[0].dominant_name,
    classified_at:  rows[0].classified_at,
    country:        rows[0].country,
    state:          rows[0].state,
    district:       rows[0].district,
  });
});

module.exports = router;
