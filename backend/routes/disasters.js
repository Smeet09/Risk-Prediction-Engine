const express = require("express");
const router  = express.Router();
const pool    = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const multer  = require("multer");
const path    = require("path");
const axios   = require("axios");
const FormData = require("form-data");

// ─── GET /api/disasters ────────────────────────────────────────────────────────
// List disaster types (optionally filter by category, active only)
router.get("/", protect, async (req, res) => {
  const { category, all } = req.query;
  let query = `SELECT * FROM disaster_types`;
  const params = [];
  const conditions = [];

  // Normal users only see active disasters; admin can pass ?all=1
  if (!all || req.user?.role !== "admin") {
    conditions.push(`is_active = TRUE`);
  }
  if (category) {
    params.push(category);
    conditions.push(`category = $${params.length}`);
  }
  if (conditions.length) query += ` WHERE ${conditions.join(" AND ")}`;
  query += ` ORDER BY sort_order, name`;

  const { rows } = await pool.query(query, params);

  // Group by category
  const grouped = {};
  rows.forEach(r => {
    if (!grouped[r.category]) grouped[r.category] = [];
    grouped[r.category].push(r);
  });

  res.json({ disasters: rows, grouped });
});

// ─── GET /api/disasters/:code ─────────────────────────────────────────────────
router.get("/:code", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT * FROM disaster_types WHERE code=$1`, [req.params.code]
  );
  if (!rows.length) return res.status(404).json({ error: "Disaster type not found" });
  res.json({ disaster: rows[0] });
});

// ─── POST /api/disasters ──────────────────────────────────────────────────────
// Admin: create a new disaster type
router.post("/", protect, adminOnly, async (req, res) => {
  const { name, code, category, description, icon, color, sort_order, default_weights } = req.body;
  if (!name || !code || !category)
    return res.status(400).json({ error: "name, code, and category are required" });

  const validCategories = ["Hydro-meteorological", "Geological", "Climatological", "Biological", "Other"];
  if (!validCategories.includes(category))
    return res.status(400).json({ error: `category must be one of: ${validCategories.join(", ")}` });

  try {
    const { rows } = await pool.query(
      `INSERT INTO disaster_types
         (name, code, category, description, icon, color, sort_order, default_weights)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
       RETURNING *`,
      [name, code.toLowerCase().replace(/\s+/g, "_"),
       category, description || null,
       icon || "⚠️", color || "#0071e3",
       sort_order || 99,
       JSON.stringify(default_weights || {})]
    );
    res.status(201).json({ disaster: rows[0] });
  } catch (err) {
    if (err.code === "23505")
      return res.status(409).json({ error: "Disaster code already exists" });
    throw err;
  }
});

// ─── PATCH /api/disasters/:code ───────────────────────────────────────────────
// Admin: update a disaster type (toggle active, update weights, etc.)
router.patch("/:code", protect, adminOnly, async (req, res) => {
  const allowed = ["name", "category", "description", "icon", "color",
                   "sort_order", "default_weights", "is_active"];
  const updates = [];
  const values  = [];

  allowed.forEach(field => {
    if (req.body[field] !== undefined) {
      values.push(
        field === "default_weights" ? JSON.stringify(req.body[field]) : req.body[field]
      );
      updates.push(`${field}=$${values.length}`);
    }
  });

  if (!updates.length)
    return res.status(400).json({ error: "No valid fields to update" });

  values.push(req.params.code);
  const { rows } = await pool.query(
    `UPDATE disaster_types SET ${updates.join(", ")}, updated_at=NOW()
     WHERE code=$${values.length} RETURNING *`,
    values
  );
  if (!rows.length) return res.status(404).json({ error: "Disaster not found" });
  res.json({ disaster: rows[0] });
});

// ─── DELETE /api/disasters/:code ──────────────────────────────────────────────
// Admin: permanently delete (prefer is_active=false to just hide)
router.delete("/:code", protect, adminOnly, async (req, res) => {
  const { rows } = await pool.query(
    `DELETE FROM disaster_types WHERE code=$1 RETURNING code`, [req.params.code]
  );
  if (!rows.length) return res.status(404).json({ error: "Disaster not found" });
  res.json({ message: `Disaster '${req.params.code}' deleted` });
});


// ─── POST /api/disasters/:code/upload-script ─────────────────────────────────
// Admin: upload a custom Python susceptibility script for a disaster type
const scriptUpload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 }, // 10 MB max
  fileFilter: (_req, file, cb) => {
    if (path.extname(file.originalname).toLowerCase() === ".py") cb(null, true);
    else cb(new Error("Only .py files are allowed"));
  },
});

router.post("/:code/upload-script", protect, adminOnly, scriptUpload.single("script"), async (req, res) => {
  const { code } = req.params;
  if (!req.file) return res.status(400).json({ error: "No script file uploaded" });

  // Verify disaster exists
  const { rows } = await pool.query(`SELECT * FROM disaster_types WHERE code=$1`, [code]);
  if (!rows.length) return res.status(404).json({ error: "Disaster type not found" });

  const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";

  try {
    // Forward the .py file to the GIS service
    const form = new FormData();
    form.append("file", req.file.buffer, {
      filename:    req.file.originalname,
      contentType: "text/x-python",
    });
    form.append("disaster_code", code);

    const gisRes = await axios.post(`${GIS_URL}/api/susceptibility/upload-script`, form, {
      headers: form.getHeaders(),
      timeout: 30000,
    });

    const { script_path } = gisRes.data;

    // Persist the script path in disaster_types
    await pool.query(
      `UPDATE disaster_types SET script_path=$1, updated_at=NOW() WHERE code=$2`,
      [script_path, code]
    );

    res.json({
      message:     `Script uploaded for '${code}'`,
      script_path,
      filename:    gisRes.data.filename,
    });
  } catch (err) {
    const detail = err.response?.data?.detail || err.message;
    res.status(500).json({ error: `Script upload failed: ${detail}` });
  }
});

module.exports = router;
