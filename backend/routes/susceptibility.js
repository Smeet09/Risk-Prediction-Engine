const express  = require("express");
const router   = express.Router();
const pool     = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const axios    = require("axios");
const { v4: uuidv4 } = require("uuid");
const { getGlobalManualReadiness } = require("./manual");

// ─── GET /api/susceptibility ──────────────────────────────────────────────────
// List all results (latest per region+disaster)
router.get("/", protect, async (req, res) => {
  const { disaster_code, region_id } = req.query;
  let q = `
    SELECT sr.id, sr.disaster_code, sr.generated_at, sr.status,
           sr.dominant_class, sr.class_stats,
           r.country, r.state, r.district,
           dt.name AS disaster_name, dt.icon, dt.color, dt.category
    FROM susceptibility_results sr
    JOIN regions r ON r.id = sr.region_id
    LEFT JOIN disaster_types dt ON dt.code = sr.disaster_code
    WHERE 1=1`;
  const params = [];
  if (disaster_code) { params.push(disaster_code); q += ` AND sr.disaster_code=$${params.length}`; }
  if (region_id)     { params.push(region_id);     q += ` AND sr.region_id=$${params.length}::uuid`; }
  q += ` ORDER BY sr.generated_at DESC`;

  const { rows } = await pool.query(q, params);
  res.json({ results: rows });
});

// ─── GET /api/susceptibility/:regionId ───────────────────────────────────────
// All disasters for one region
router.get("/:regionId", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT sr.*, dt.name AS disaster_name, dt.icon, dt.color, dt.category
     FROM susceptibility_results sr
     LEFT JOIN disaster_types dt ON dt.code = sr.disaster_code
     WHERE sr.region_id=$1
     ORDER BY sr.generated_at DESC`,
    [req.params.regionId]
  );
  res.json({ results: rows });
});

// ─── GET /api/susceptibility/:regionId/:disasterCode ─────────────────────────
router.get("/:regionId/:disasterCode", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT sr.*, dt.name AS disaster_name, dt.icon, dt.color
     FROM susceptibility_results sr
     LEFT JOIN disaster_types dt ON dt.code = sr.disaster_code
     WHERE sr.region_id=$1 AND sr.disaster_code=$2
     ORDER BY sr.generated_at DESC LIMIT 1`,
    [req.params.regionId, req.params.disasterCode]
  );
  if (!rows.length) return res.status(404).json({ error: "No susceptibility result found" });
  res.json({ result: rows[0] });
});

// ─── POST /api/susceptibility/generate ───────────────────────────────────────
// Admin: generate disaster-wise susceptibility for a region
router.post("/generate", protect, adminOnly, async (req, res) => {
  const { region_id, disaster_code, terrain_weights } = req.body;
  if (!region_id || !disaster_code)
    return res.status(400).json({ error: "region_id and disaster_code are required" });

  // Validate region exists
  const { rows: regionRows } = await pool.query(
    `SELECT r.*, di.terrain_ready, di.manual_india_ready
     FROM regions r LEFT JOIN data_inventory di ON di.region_id=r.id
     WHERE r.id=$1`, [region_id]
  );
  if (!regionRows.length) return res.status(404).json({ error: "Region not found" });
  const region = regionRows[0];

  if (!region.terrain_ready) {
    return res.status(422).json({
      error: "Terrain classification not ready. Please upload and process a DEM first.",
      prerequisite: "terrain_ready",
    });
  }

  const isManualReady = await getGlobalManualReadiness();
  if (!isManualReady) {
    return res.status(422).json({
      error: "India-wide manual data is missing or incomplete. Please upload all required layers in the Manual Data section first.",
      prerequisite: "manual_india_ready",
    });
  }

  // Validate disaster type exists and is active
  const { rows: dtRows } = await pool.query(
    `SELECT * FROM disaster_types WHERE code=$1 AND is_active=TRUE`, [disaster_code]
  );
  if (!dtRows.length)
    return res.status(404).json({ error: `Disaster type '${disaster_code}' not found or inactive` });
  const disaster = dtRows[0];
  const scriptPath = disaster.script_path || null;  // custom uploaded script path (optional)

  // Merge weights: default_weights → override with provided terrain_weights
  const weights = Object.assign({}, disaster.default_weights || {}, terrain_weights || {});

  // Create/update susceptibility record
  const DATA_ROOT = req.app.locals.DATA_ROOT;
  const jobId = uuidv4();

  // Create pending record
  await pool.query(
    `INSERT INTO susceptibility_results
       (region_id, disaster_type_id, disaster_code, terrain_weights, status, log)
     VALUES ($1,$2,$3,$4,'pending','Job queued')
     ON CONFLICT (region_id, disaster_code) DO UPDATE SET
       terrain_weights=$4, status='pending', log='Re-running...', generated_at=NOW()`,
    [region_id, disaster.id, disaster_code, JSON.stringify(weights)]
  );

  // Create job tracking entry
  await pool.query(
    `INSERT INTO jobs (id, region_id, module, disaster_type, status, log)
     VALUES ($1,$2,'susceptibility',$3,'pending','Queued')`,
    [jobId, region_id, disaster_code]
  );

  // Trigger async processing
  _triggerSusceptibility(req.app, jobId, region_id, region, disaster_code, weights, DATA_ROOT, scriptPath);

  res.status(202).json({
    jobId,
    message: `${disaster.name} susceptibility generation started`,
    disaster: { name: disaster.name, code: disaster.code, icon: disaster.icon },
    terrain_weights: weights,
  });
});

// ─── Async susceptibility processing ─────────────────────────────────────────
async function _triggerSusceptibility(app, jobId, regionId, region, disasterCode, weights, dataRoot, scriptPath = null) {
  const GIS_URL  = process.env.GIS_SERVICE_URL || "http://localhost:8000";
  const broadcast = app.locals.broadcastJob;

  const _update = async (status, progress, log) => {
    await pool.query(
      `UPDATE jobs SET status=$1, progress=$2, log=$3, updated_at=NOW() WHERE id=$4`,
      [status, progress, log, jobId]
    );
    broadcast(jobId, { status, progress, log });
  };

  try {
    await _update("processing", 10, `Generating ${disasterCode} susceptibility map...`);

    const { data } = await axios.post(`${GIS_URL}/api/susceptibility/generate-susceptibility`, {
      job_id:          jobId,
      region_id:       regionId,
      disaster_code:   disasterCode,
      country:         region.country,
      state:           region.state,
      district:        region.district,
      terrain_weights: weights,
      data_root:       dataRoot,
      script_path:     scriptPath,
    }, { timeout: 60 * 60 * 1000 });

    // Store result
    await pool.query(
      `UPDATE susceptibility_results SET
         tif_path      = $1,
         geojson_path  = $2,
         final_geojson = $3,
         class_stats   = $4,
         status        = 'done',
         log           = $5,
         generated_at  = NOW()
       WHERE region_id=$6 AND disaster_code=$7`,
      [data.tif_path || null, data.geojson_path || null,
       data.geojson ? JSON.stringify(data.geojson.final) : null,
       data.class_stats ? JSON.stringify(data.class_stats) : null,
       data.log || "Completed",
       regionId, disasterCode]
    );

    // Mark susceptibility_ready in inventory
    await pool.query(
      `UPDATE data_inventory SET susceptibility_ready=TRUE, updated_at=NOW() WHERE region_id=$1`,
      [regionId]
    );

    await _update("done", 100, `✓ ${disasterCode} susceptibility map generated successfully`);
  } catch (err) {
    const msg = err.response?.data?.detail || err.message;
    await pool.query(
      `UPDATE susceptibility_results SET status='failed', log=$1 WHERE region_id=$2 AND disaster_code=$3`,
      [msg, regionId, disasterCode]
    );
    await _update("failed", 0, `Failed: ${msg}`);
  }
}

module.exports = router;
