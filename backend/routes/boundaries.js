const express = require("express");
const router  = express.Router();
const pool    = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const { spawn } = require("child_process");
const path   = require("path");
const fs     = require("fs");

// ─── GET /api/boundaries/status ───────────────────────────────────────────────
// Returns import counts per level across the 4 explicitly partitioned tables
router.get("/status", async (_req, res) => {
  const getCount = async (table) => {
    try {
      const { rows } = await pool.query(`SELECT COUNT(*)::int AS count FROM ${table}`);
      return rows[0].count;
    } catch(e) { return 0; }
  };
  
  const counts = {
    state:    await getCount("states_boundaries"),
    district: await getCount("districts_boundaries"),
    taluka:   await getCount("talukas_boundaries"),
    village:  await getCount("villages_boundaries")
  };
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  res.json({ counts, total, imported: total > 0 });
});

// ─── GET /api/boundaries/states ───────────────────────────────────────────────
router.get("/states", protect, async (_req, res) => {
  const { rows } = await pool.query(
    `SELECT DISTINCT ON (name) name AS state,
            ST_AsGeoJSON(centroid) AS centroid
     FROM states_boundaries
     ORDER BY name`
  );
  res.json({ states: rows.map(r => ({ name: r.state, centroid: r.centroid ? JSON.parse(r.centroid) : null })) });
});

// ─── GET /api/boundaries/districts/:state ─────────────────────────────────────
router.get("/districts/:state", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT DISTINCT ON (name) name AS district,
            ST_AsGeoJSON(centroid) AS centroid
     FROM districts_boundaries
     WHERE state_name = $1
     ORDER BY name`,
    [req.params.state]
  );
  res.json({ districts: rows.map(r => ({ name: r.district, centroid: r.centroid ? JSON.parse(r.centroid) : null })) });
});

// ─── GET /api/boundaries/talukas/:state/:district ─────────────────────────────
router.get("/talukas/:state/:district", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT DISTINCT ON (name) name AS taluka,
            ST_AsGeoJSON(centroid) AS centroid
     FROM talukas_boundaries
     WHERE state_name    = $1
       AND district_name = $2
     ORDER BY name`,
    [req.params.state, req.params.district]
  );
  res.json({ talukas: rows.map(r => ({ name: r.taluka, centroid: r.centroid ? JSON.parse(r.centroid) : null })) });
});

// ─── GET /api/boundaries/villages/:state/:district/:taluka ────────────────────
router.get("/villages/:state/:district/:taluka", protect, async (req, res) => {
  const { rows } = await pool.query(
    `SELECT DISTINCT ON (name) name AS village,
            ST_AsGeoJSON(centroid) AS centroid
     FROM villages_boundaries
     WHERE state_name    = $1
       AND district_name = $2
       AND taluka_name   = $3
     ORDER BY name`,
    [req.params.state, req.params.district, req.params.taluka]
  );
  res.json({ villages: rows.map(r => ({ name: r.village, centroid: r.centroid ? JSON.parse(r.centroid) : null })) });
});

// ─── POST /api/boundaries/import ──────────────────────────────────────────────
// Admin-only one-time importer. Runs import_boundaries.py as a child process.
// Note: Kept for legacy local dataset mounting if ever needed.
router.post("/import", protect, adminOnly, async (req, res) => {
  const { skip_village = false, levels } = req.body;
  const PROJECT_ROOT  = path.resolve(__dirname, "..", "..");
  const SCRIPT_PATH   = path.join(PROJECT_ROOT, "gis-service", "scripts", "import_boundaries.py");
  const DATASET_DIR   = path.join(PROJECT_ROOT, "Dataset");
  const PYTHON        = process.env.PYTHON_PATH || "python";
  const DATABASE_URL  = process.env.DATABASE_URL || "";

  // Stream back log via JSON lines
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  res.setHeader("Transfer-Encoding", "chunked");

  const args = [SCRIPT_PATH, "--dataset-dir", DATASET_DIR, "--db-url", DATABASE_URL];
  if (skip_village)          args.push("--skip-village");
  if (levels?.length)        args.push("--levels", ...levels);

  const proc = spawn(PYTHON, args, { env: { ...process.env, DATABASE_URL } });
  proc.stdout.on("data", chunk => res.write(chunk.toString()));
  proc.stderr.on("data", chunk => res.write(`[ERR] ${chunk.toString()}`));
  proc.on("close", code => { res.write(`\n[EXIT] Import process exited with code ${code}\n`); res.end(); });
  proc.on("error", err => { res.write(`\n[SPAWN ERROR] ${err.message}\n`); res.end(); });
});

// ─── MULTER SETUP FOR BOUNDARY ZIP UPLOADS ────────────────────────────────────
const multer = require("multer");
const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    const uploadPath = path.join(req.app.locals.DATA_ROOT, "uploads", "boundaries");
    if (!fs.existsSync(uploadPath)) fs.mkdirSync(uploadPath, { recursive: true });
    cb(null, uploadPath);
  },
  filename: (req, file, cb) => {
    cb(null, `boundary_${Date.now()}_${file.originalname}`);
  }
});
const upload = multer({ storage });

// ─── POST /api/boundaries/upload-zip ──────────────────────────────────────────
// Dynamic streaming upload endpoint explicitly for bounded Zip insertions
router.post("/upload-zip", protect, adminOnly, upload.single("file"), async (req, res) => {
  if (!req.file) return res.status(400).json({ error: "No zip file uploaded" });
  
  const level = req.body.level || "state";
  const overwrite = req.body.overwrite === "true"; // Multipart parsing limitation
  
  const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
  const SCRIPT_PATH  = path.join(PROJECT_ROOT, "gis-service", "scripts", "import_zip.py");
  const PYTHON       = process.env.PYTHON_PATH || "python";
  const DATABASE_URL = process.env.DATABASE_URL || "";

  if (!fs.existsSync(SCRIPT_PATH)) {
    return res.status(404).json({ error: "import_zip.py parsing mechanism missing." });
  }

  // Stream log chunk output exactly mimicking real-world behavior
  res.setHeader("Content-Type", "text/plain; charset=utf-8");
  res.setHeader("Transfer-Encoding", "chunked");

  const args = [SCRIPT_PATH, "--zip-path", req.file.path, "--level", level, "--db-url", DATABASE_URL];
  if (overwrite) args.push("--overwrite");

  const proc = spawn(PYTHON, args, { env: { ...process.env, DATABASE_URL } });
  
  proc.stdout.on("data", chunk => res.write(chunk.toString()));
  proc.stderr.on("data", chunk => res.write(`[ERR] ${chunk.toString()}`));
  
  proc.on("close", code => {
    res.write(`\n[EXIT] Import process exited with code ${code}\n`);
    res.end();
  });
  
  proc.on("error", err => {
    res.write(`\n[SPAWN ERROR] ${err.message}\n`);
    res.end();
  });
});

module.exports = router;
