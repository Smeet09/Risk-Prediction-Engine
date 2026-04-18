const express = require("express");
const router = express.Router();
const pool = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const axios = require("axios");
const { v4: uuidv4 } = require("uuid");
const fs = require("fs");
const path = require("path");

// GET /api/jobs/:id — get job status
router.get("/:id", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT j.*, 
       COALESCE(j.country, r.country) as country, 
       COALESCE(j.state, r.state) as state, r.district
     FROM jobs j LEFT JOIN regions r ON r.id = j.region_id
     WHERE j.id = $1`,
    [req.params.id]
  );
  if (rows.length === 0) return res.status(404).json({ error: "Job not found" });
  res.json({ job: rows[0] });
});

// GET /api/jobs — list recent jobs (admin)
router.get("/", protect, adminOnly, async (req, res) => {
  const limit = parseInt(req.query.limit) || 20;
  const { rows } = await pool.query(
    `SELECT j.id, j.module, j.disaster_type, j.status, j.progress,
       j.created_at, j.updated_at, j.year, j.month,
       COALESCE(j.country, r.country) as country, 
       COALESCE(j.state, r.state) as state, r.district
     FROM jobs j LEFT JOIN regions r ON r.id = j.region_id
     ORDER BY j.created_at DESC LIMIT $1`,
    [limit]
  );
  res.json({ jobs: rows });
});

// POST /api/jobs/susceptibility — trigger susceptibility generation
router.post("/susceptibility", protect, adminOnly, async (req, res) => {
  const { region_id, disaster_type } = req.body;
  if (!region_id || !disaster_type) {
    return res.status(400).json({ error: "region_id and disaster_type are required" });
  }

  // Verify all data is ready
  const { rows: inv } = await pool.query(
    `SELECT dem_ready, exposure_ready, manual_ready
     FROM data_inventory WHERE region_id = $1`,
    [region_id]
  );
  if (!inv[0] || !inv[0].dem_ready || !inv[0].exposure_ready || !inv[0].manual_ready) {
    return res.status(400).json({ error: "Not all data is ready for this region" });
  }

  const jobId = uuidv4();
  await pool.query(
    `INSERT INTO jobs (id, region_id, module, disaster_type, status, log)
     VALUES ($1,$2,'susceptibility',$3,'pending','Job queued')`,
    [jobId, region_id, disaster_type]
  );

  // Trigger async
  triggerSusceptibility(req.app, jobId, region_id, disaster_type);

  res.status(202).json({ jobId, message: "Susceptibility job queued" });
});

async function triggerSusceptibility(app, jobId, regionId, disasterType) {
  const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";
  const broadcast = app.locals.broadcastJob;

  try {
    await pool.query(
      `UPDATE jobs SET status='processing', log='Susceptibility engine started...', updated_at=NOW() WHERE id=$1`,
      [jobId]
    );
    broadcast(jobId, { status: "processing", progress: 5, log: "Susceptibility engine started..." });

    // Get region details for DATA_ROOT resolution
    const DATA_ROOT = app.locals.DATA_ROOT;
    const { rows: reg } = await pool.query(`SELECT * FROM regions WHERE id=$1`, [regionId]);
    const region = reg[0];

    const response = await axios.post(`${GIS_URL}/api/susceptibility/generate-susceptibility`, {
      job_id: jobId,
      region_id: regionId,
      disaster_type: disasterType,
      country: region.country,
      state: region.state,
      district: region.district,
      data_root: DATA_ROOT,
    }, { timeout: 60 * 60 * 1000 }); // 1 hour

    const result = response.data;

    await pool.query(
      `UPDATE jobs SET status='done', progress=100, log=$1, updated_at=NOW() WHERE id=$2`,
      [result.log || "Susceptibility generated", jobId]
    );
    await pool.query(
      `UPDATE data_inventory SET susceptibility_ready=TRUE, updated_at=NOW() WHERE region_id=$1`,
      [regionId]
    );

    // Store result reference
    if (result.geojson) {
      await pool.query(
        `INSERT INTO susceptibility_results (region_id, disaster_type, final_geojson, hazard_geojson, exposure_geojson, vuln_geojson)
         VALUES ($1,$2,$3,$4,$5,$6)
         ON CONFLICT DO NOTHING`,
        [
          regionId, disasterType,
          JSON.stringify(result.geojson?.final),
          JSON.stringify(result.geojson?.hazard),
          JSON.stringify(result.geojson?.exposure),
          JSON.stringify(result.geojson?.vulnerability),
        ]
      );
    }

    broadcast(jobId, { status: "done", progress: 100, log: result.log || "Done" });
  } catch (err) {
    const errMsg = err.response?.data?.detail || err.message;
    await pool.query(
      `UPDATE jobs SET status='failed', log=$1, updated_at=NOW() WHERE id=$2`,
      [errMsg, jobId]
    );
    broadcast(jobId, { status: "failed", log: errMsg });
  }
}

// POST /api/jobs/sync — verify exact status of all 'processing' jobs with GIS service
router.post("/sync", protect, adminOnly, async (req, res) => {
  const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";

  try {
    // 1. Get real-time active jobs from GIS
    const response = await axios.get(`${GIS_URL}/status/active-jobs`);
    const activeJobIds = response.data.active_jobs || [];

    // 2. Find all jobs we THINK are processing
    const { rows: processingJobs } = await pool.query(
      "SELECT id FROM jobs WHERE status = 'processing'"
    );

    let updatedCount = 0;
    for (const job of processingJobs) {
      // 3. If it's NOT in the active GIS list, it means it crashed/stopped
      if (!activeJobIds.includes(job.id)) {
        await pool.query(
          "UPDATE jobs SET status = 'failed', log = 'Sync: Job was found dead/terminated.', updated_at = NOW() WHERE id = $1",
          [job.id]
        );
        updatedCount++;
      }
    }

    res.json({
      message: "Sync complete",
      active_in_gis: activeJobIds.length,
      corrected_dead_jobs: updatedCount
    });
  } catch (err) {
    const errMsg = err.response?.data?.detail || err.message;
    res.status(500).json({ error: `Sync failed: ${errMsg}` });
  }
});

// POST /api/jobs/sync-integrity — deep verify physical data exists on disk
router.post("/sync-integrity", protect, adminOnly, async (req, res) => {
  const DATA_ROOT = req.app.locals.DATA_ROOT;

  function _safe(s) {
    return (s || "_none").replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  try {
    const { rows: regions } = await pool.query(`SELECT id, country, state, district FROM regions`);
    let correctedCount = 0;

    for (const r of regions) {
      const destDir = path.join(
        DATA_ROOT,
        _safe(r.country),
        _safe(r.state),
        r.district ? _safe(r.district) : "_state_level",
        "dem_features"
      );

      const exists = fs.existsSync(destDir);
      console.log(`[Sync-Integrity] Checking path: ${destDir} - Exists: ${exists}`);

      // If folder is gone, reset statuses
      if (!exists) {
        console.warn(`[Sync-Integrity] Data missing for region ${r.id} (${r.state}). Resetting status.`);
        const { rowCount } = await pool.query(
          `UPDATE data_inventory 
           SET dem_ready=FALSE, terrain_ready=FALSE, manual_india_ready=FALSE, susceptibility_ready=FALSE, updated_at=NOW()
           WHERE region_id=$1 AND (dem_ready=TRUE OR terrain_ready=TRUE OR susceptibility_ready=TRUE)`,
          [r.id]
        );
        if (rowCount > 0) correctedCount++;

        // Also cleanup related tables
        await pool.query(`DELETE FROM dem_uploads WHERE region_id=$1`, [r.id]);
        await pool.query(`DELETE FROM topographic_features WHERE region_id=$1`, [r.id]);
        await pool.query(`DELETE FROM terrain_classifications WHERE region_id=$1`, [r.id]);
        await pool.query(`DELETE FROM susceptibility_results WHERE region_id=$1`, [r.id]);
      }
    }

    res.json({
      message: "Integrity sync complete",
      corrected_regions: correctedCount
    });
  } catch (err) {
    res.status(500).json({ error: `Integrity sync failed: ${err.message}` });
  }
});


module.exports = router;
