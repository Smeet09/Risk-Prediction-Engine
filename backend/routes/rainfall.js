const express = require("express");
const router = express.Router();
const pool = require("../config/db");
const { protect } = require("../middleware/auth");
const axios = require("axios");

// GET /api/rainfall/check?region_id=&date=YYYY-MM-DD
router.get("/check", protect, async (req, res) => {
  const { region_id, date } = req.query;
  if (!region_id || !date) {
    return res.status(400).json({ error: "region_id and date are required" });
  }

  const { rows } = await pool.query(
    `SELECT COUNT(*) as cnt FROM rainfall_timeseries
     WHERE region_id=$1 AND date=$2`,
    [region_id, date]
  );

  const exists = parseInt(rows[0].cnt) > 0;
  res.json({ exists, region_id, date });
});

// GET /api/rainfall/data?region_id=&date=YYYY-MM-DD  → 24-hour timeseries
router.get("/data", protect, async (req, res) => {
  const { region_id, date } = req.query;
  if (!region_id || !date) {
    return res.status(400).json({ error: "region_id and date are required" });
  }

  const { rows } = await pool.query(
    `SELECT hour, rainfall_mm, ST_AsGeoJSON(geom)::json AS geojson
     FROM rainfall_timeseries
     WHERE region_id=$1 AND date=$2
     ORDER BY hour`,
    [region_id, date]
  );

  res.json({ timeseries: rows, region_id, date });
});

// POST /api/rainfall/fetch — trigger ERA5 download for a region+date (admin)
router.post("/fetch", protect, async (req, res) => {
  const { region_id, date } = req.body;
  if (!region_id || !date) {
    return res.status(400).json({ error: "region_id and date are required" });
  }

  const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";

  // Get region bbox for ERA5 area
  const { rows: reg } = await pool.query(
    `SELECT country, state, district, ST_AsGeoJSON(bbox)::json AS bbox
     FROM regions WHERE id=$1`,
    [region_id]
  );
  if (reg.length === 0) return res.status(404).json({ error: "Region not found" });

  // Fire-and-forget ERA5 fetch
  res.status(202).json({ message: "ERA5 data fetch initiated", region_id, date });

  try {
    const response = await axios.post(`${GIS_URL}/fetch-era5`, {
      region_id,
      date,
      bbox: reg[0].bbox,
      country: reg[0].country,
      state: reg[0].state,
      district: reg[0].district,
    }, { timeout: 30 * 60 * 1000 });

    // Store returned timeseries in DB
    const ts = response.data.timeseries || [];
    for (const entry of ts) {
      await pool.query(
        `INSERT INTO rainfall_timeseries (region_id, date, hour, rainfall_mm)
         VALUES ($1,$2,$3,$4)
         ON CONFLICT (region_id, date, hour) DO UPDATE SET rainfall_mm=EXCLUDED.rainfall_mm`,
        [region_id, date, entry.hour, entry.rainfall_mm]
      );
    }
  } catch (err) {
    console.error("ERA5 fetch error:", err.message);
  }
});

module.exports = router;
