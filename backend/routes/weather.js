const express = require("express");
const router = express.Router();
const axios = require("axios");
const pool = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");

const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";

router.post("/download", protect, adminOnly, async (req, res) => {
  const { country, state, year, month } = req.body;

  if (!country || !state || !year || !month) {
    return res.status(400).json({ error: "Missing required parameters (country, state, year, month)." });
  }

  // Create job in DB
  let jobResult;
  try {
    const { rows } = await pool.query(
      `INSERT INTO jobs (module, status, country, state, year, month, progress, log, created_at, updated_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
       RETURNING id`,
      ["weather", "pending", country, state, year, month, 0, "Job created."]
    );
    jobResult = rows[0];
  } catch (error) {
    return res.status(500).json({ error: "Failed to create weather job in DB: " + error.message });
  }

  const jobId = jobResult.id;

  // Trigger GIS Service asynchronously
  axios.post(`${GIS_URL}/weather/download`, {
    country,
    state,
    year,
    month,
    job_id: jobId
  }).catch((err) => {
    // If it completely fails to reach the service, update the DB so it doesn't stay pending forever.
    console.error("GIS /weather/download endpoint error:", err.message);
    const errorMsg = err.response?.data?.detail || err.message;
    pool.query(`UPDATE jobs SET status = 'failed', log = log || E'\\nFailed to contact GIS service: ' || $1, updated_at = NOW() WHERE id = $2`, [errorMsg, jobId]).catch(() => { });
  });

  return res.status(202).json({ jobId, message: "Weather download triggered successfully" });
});

router.get("/status", protect, adminOnly, async (req, res) => {
  try {
    const { rows } = await pool.query(
      `SELECT id, country, state, year, month, status 
       FROM jobs 
       WHERE module = 'weather' AND year IS NOT NULL AND month IS NOT NULL
       ORDER BY created_at DESC LIMIT 50`
    );
    res.json({ statuses: rows });
  } catch (error) {
    res.status(500).json({ error: "Failed to fetch weather status: " + error.message });
  }
});

module.exports = router;
