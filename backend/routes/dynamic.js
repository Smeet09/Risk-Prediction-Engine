const express   = require("express");
const router    = express.Router();
const pool      = require("../config/db");
const axios     = require("axios");
const { v4: uuidv4 } = require("uuid");
const { protect } = require("../middleware/auth");

const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";

// ─── POST /api/dynamic/predict ────────────────────────────────────────────────
router.post("/predict", protect, async (req, res) => {
  const { region_id, disaster_code, target_date } = req.body;
  if (!region_id || !disaster_code || !target_date)
    return res.status(400).json({ error: "region_id, disaster_code, and target_date are required" });

  // Get region details
  let region;
  try {
    const { rows } = await pool.query(
      "SELECT id, country, state, district FROM regions WHERE id=$1",
      [region_id]
    );
    if (!rows.length) return res.status(404).json({ error: "Region not found" });
    region = rows[0];
  } catch (err) {
    return res.status(500).json({ error: "DB error: " + err.message });
  }

  if (!region.state)
    return res.status(422).json({ error: "Region has no state — cannot query weather data." });

  // ─── Cache & Active Job Check ──────────────────────────────────────────────
  try {
    // 1. Check if result already exists in the metadata table
    const { rows: existing } = await pool.query(
      "SELECT id FROM dynamic_risk_results WHERE region_id=$1 AND disaster_code=$2 AND target_date=$3",
      [region_id, disaster_code, target_date]
    );

    if (existing.length > 0) {
      const jobId = uuidv4();
      await pool.query(
        `INSERT INTO jobs (id, region_id, module, disaster_type, status, progress, log)
         VALUES ($1, $2, 'dynamic', $3, 'done', 100, 'Result already exists in database. Loaded from spatial cache.')`,
        [jobId, region_id, disaster_code]
      );
      return res.status(202).json({
        jobId,
        message: `Existing result found. Loading...`,
        region: { country: region.country, state: region.state, district: region.district },
      });
    }

    // 2. Check if a job is ALREADY processing for this exact parameters
    // We look for 'pending' or 'processing' jobs in the same region/date/code
    // Note: Since 'jobs' table doesn't have 'target_date', we check the log for the date string as a fallback 
    // OR we just allow it if it's the exact same region and disaster if we want to be strict.
    // Better: Check for recent jobs in the same region/disaster that are not failed.
    const { rows: activeJobs } = await pool.query(
      `SELECT id FROM jobs 
       WHERE region_id=$1 AND disaster_type=$2 AND module='dynamic' 
       AND status IN ('pending', 'processing')
       AND (config->>'targetDate') = $3
       ORDER BY created_at DESC LIMIT 1`,
      [region_id, disaster_code, targetDate]
    );

    if (activeJobs.length > 0) {
      return res.status(202).json({
        jobId: activeJobs[0].id,
        message: "A prediction job is already in progress for this region. Joining...",
        region: { country: region.country, state: region.state, district: region.district },
      });
    }

  } catch (err) {
    console.error("[Dynamic Route] Cache/Job check error:", err);
  }

  // ─── Create actual job ──────────────────────────────────────────────────────
  const jobId = uuidv4();
  try {
    await pool.query(
      `INSERT INTO jobs (id, region_id, module, disaster_type, status, log)
       VALUES ($1, $2, 'dynamic', $3, 'pending', $4)`,
      [jobId, region_id, disaster_code, `Job queued for dynamic ${disaster_code} risk prediction for ${target_date}`]
    );
  } catch (err) {
    return res.status(500).json({ error: "Failed to create job: " + err.message });
  }

  const broadcast = req.app.locals.broadcastJob;
  _triggerDynamic(jobId, region, disaster_code, target_date, broadcast);

  return res.status(202).json({
    jobId,
    message: `Dynamic ${disaster_code} prediction started for ${target_date}`,
    region: { country: region.country, state: region.state, district: region.district },
  });
});

// ─── Async trigger ────────────────────────────────────────────────────────────
async function _triggerDynamic(jobId, region, disasterCode, targetDate, broadcast) {
  const _update = async (status, progress, log) => {
    await pool.query(
      "UPDATE jobs SET status=$1, progress=$2, log=$3, updated_at=NOW() WHERE id=$4",
      [status, progress, log, jobId]
    );
    if (broadcast) broadcast(jobId, { status, progress, log });
  };

  try {
    await _update("processing", 5, `Starting dynamic ${disasterCode} prediction…`);

    await axios.post(`${GIS_URL}/dynamic/predict`, {
      job_id:          jobId,
      region_id:       region.id,
      disaster_code:   disasterCode,
      state:           region.state,
      country:         region.country  || "",
      district:        region.district || "",
      target_date:     targetDate,
      antecedent_days: 10,
    }, { timeout: 30 * 60 * 1000 });

    // job status is updated inside GIS service via _log_job
  } catch (err) {
    const msg = err.response?.data?.detail || err.message;
    await _update("failed", 0, `Failed: ${msg}`);
  }
}

// ─── GET /api/dynamic/result/:regionId/:disasterCode/:date ───────────────────
// Reads from PostGIS via GIS service (mirrors susceptibility read flow)
router.get("/result/:regionId/:disasterCode/:date", protect, async (req, res) => {
  const { regionId, disasterCode, date } = req.params;
  try {
    const { data } = await axios.get(
      `${GIS_URL}/dynamic/result/${regionId}/${disasterCode}/${date}`,
      { timeout: 60000 }
    );
    res.json(data);
  } catch (err) {
    if (err.response?.status === 404)
      return res.status(404).json({ error: "No dynamic result found for this date" });
    res.status(500).json({ error: err.response?.data?.detail || err.message });
  }
});

// ─── GET /api/dynamic/history/:regionId/:disasterCode ────────────────────────
router.get("/history/:regionId/:disasterCode", protect, async (req, res) => {
  const { regionId, disasterCode } = req.params;
  try {
    const { data } = await axios.get(
      `${GIS_URL}/dynamic/history/${regionId}/${disasterCode}`,
      { timeout: 30000 }
    );
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.response?.data?.detail || err.message });
  }
});

// ─── GET /api/dynamic/available-dates/:regionId/:disasterCode ────────────────
router.get("/available-dates/:regionId/:disasterCode", protect, async (req, res) => {
  const { regionId, disasterCode } = req.params;
  try {
    const { rows } = await pool.query("SELECT state FROM regions WHERE id=$1", [regionId]);
    if (!rows.length || !rows[0].state)
      return res.json({ available_dates: [], count: 0 });

    const { data } = await axios.get(
      `${GIS_URL}/dynamic/available-dates/${regionId}/${disasterCode}/${encodeURIComponent(rows[0].state)}`,
      { timeout: 15000 }
    );
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.response?.data?.detail || err.message });
  }
});

module.exports = router;
