const express   = require("express");
const router    = express.Router();
const pool      = require("../config/db");
const axios     = require("axios");
const { v4: uuidv4 } = require("uuid");
const { protect } = require("../middleware/auth");

const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";

// ─── POST /api/dynamic/predict ────────────────────────────────────────────────
// Trigger dynamic risk prediction for a region + disaster + target date
router.post("/predict", protect, async (req, res) => {
  const { region_id, disaster_code, target_date } = req.body;
  if (!region_id || !disaster_code || !target_date) {
    return res.status(400).json({
      error: "region_id, disaster_code, and target_date are required"
    });
  }

  // Validate region exists and get state name for weather DB query
  let region;
  try {
    const { rows } = await pool.query(
      `SELECT r.id, r.country, r.state, r.district
       FROM regions r WHERE r.id = $1`,
      [region_id]
    );
    if (!rows.length) return res.status(404).json({ error: "Region not found" });
    region = rows[0];
  } catch (err) {
    return res.status(500).json({ error: "DB error: " + err.message });
  }

  if (!region.state) {
    return res.status(422).json({
      error: "Region has no state — cannot query weather data."
    });
  }

  // Create job
  const jobId = uuidv4();
  try {
    await pool.query(
      `INSERT INTO jobs (id, region_id, module, disaster_type, status, log)
       VALUES ($1, $2, 'dynamic', $3, 'pending', 'Job queued for dynamic prediction')`,
      [jobId, region_id, disaster_code]
    );
  } catch (err) {
    return res.status(500).json({ error: "Failed to create job: " + err.message });
  }

  // Trigger GIS service asynchronously
  const broadcast = req.app.locals.broadcastJob;
  _triggerDynamic(jobId, region, disaster_code, target_date, broadcast);

  return res.status(202).json({
    jobId,
    message: `Dynamic ${disaster_code} prediction started for ${target_date}`,
    region: { country: region.country, state: region.state, district: region.district }
  });
});

// ─── Async trigger ────────────────────────────────────────────────────────────
async function _triggerDynamic(jobId, region, disasterCode, targetDate, broadcast) {
  const _update = async (status, progress, log) => {
    await pool.query(
      `UPDATE jobs SET status=$1, progress=$2, log=$3, updated_at=NOW() WHERE id=$4`,
      [status, progress, log, jobId]
    );
    if (broadcast) broadcast(jobId, { status, progress, log });
  };

  try {
    await _update("processing", 5, `Starting dynamic ${disasterCode} prediction…`);

    const { data } = await axios.post(`${GIS_URL}/dynamic/predict`, {
      job_id:          jobId,
      region_id:       region.id,
      disaster_code:   disasterCode,
      state:           region.state,
      target_date:     targetDate,
      antecedent_days: 10,
    }, { timeout: 30 * 60 * 1000 });  // 30-min timeout for large regions

    await _update("done", 100,
      `✓ Dynamic ${disasterCode} prediction complete for ${targetDate}`
    );

  } catch (err) {
    const msg = err.response?.data?.detail || err.message;
    await _update("failed", 0, `Failed: ${msg}`);
  }
}

// ─── GET /api/dynamic/result/:regionId/:disasterCode/:date ───────────────────
router.get("/result/:regionId/:disasterCode/:date", protect, async (req, res) => {
  const { regionId, disasterCode, date } = req.params;
  try {
    const { data } = await axios.get(
      `${GIS_URL}/dynamic/result/${regionId}/${disasterCode}/${date}`,
      { timeout: 15000 }
    );
    res.json(data);
  } catch (err) {
    if (err.response?.status === 404) {
      return res.status(404).json({ error: "No dynamic result found for this date" });
    }
    res.status(500).json({ error: err.response?.data?.detail || err.message });
  }
});

// ─── GET /api/dynamic/history/:regionId/:disasterCode ────────────────────────
router.get("/history/:regionId/:disasterCode", protect, async (req, res) => {
  const { regionId, disasterCode } = req.params;
  try {
    const { data } = await axios.get(
      `${GIS_URL}/dynamic/history/${regionId}/${disasterCode}`,
      { timeout: 15000 }
    );
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.response?.data?.detail || err.message });
  }
});

// ─── GET /api/dynamic/available-dates/:regionId/:disasterCode ────────────────
// Returns weather dates available in DB for this region's state
router.get("/available-dates/:regionId/:disasterCode", protect, async (req, res) => {
  const { regionId, disasterCode } = req.params;
  try {
    // Get state for this region
    const { rows } = await pool.query(
      "SELECT state FROM regions WHERE id=$1", [regionId]
    );
    if (!rows.length || !rows[0].state) {
      return res.json({ available_dates: [], count: 0 });
    }
    const state = rows[0].state;

    const { data } = await axios.get(
      `${GIS_URL}/dynamic/available-dates/${regionId}/${disasterCode}/${encodeURIComponent(state)}`,
      { timeout: 15000 }
    );
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.response?.data?.detail || err.message });
  }
});

module.exports = router;
