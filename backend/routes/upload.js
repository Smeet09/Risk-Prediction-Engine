const express = require("express");
const router  = express.Router();
const multer  = require("multer");
const path    = require("path");
const fs      = require("fs");
const { v4: uuidv4 } = require("uuid");
const pool    = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const axios   = require("axios");

// ─── Multer — DEM uploads only (State or District level) ──────────────────────
const storage = multer.diskStorage({
  destination: (req, _file, cb) => {
    const uploadDir = path.join(req.app.locals.DATA_ROOT, "uploads", "tmp");
    if (!fs.existsSync(uploadDir)) {
      fs.mkdirSync(uploadDir, { recursive: true });
    }
    cb(null, uploadDir);
  },
  filename: (_req, file, cb) => {
    cb(null, `${Date.now()}_${file.originalname}`);
  },
});

function _safe(s) {
  return (s || "_none").replace(/[^a-zA-Z0-9_-]/g, "_");
}

const upload = multer({
  storage,
  limits: { fileSize: 1024 * 1024 * 1024 }, // 1 GB
  fileFilter: (_req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    if ([".tif", ".tiff", ".asc", ".img", ".zip"].includes(ext)) {
      cb(null, true);
    } else {
      cb(new Error("Only GeoTIFF, ASCII grid, IMG, or ZIP files allowed"));
    }
  },
});

// ─── POST /api/upload/dem ─────────────────────────────────────────────────────
router.post(
  "/dem",
  protect,
  adminOnly,
  upload.single("file"),
  async (req, res) => {
    const { country, state, district } = req.body;

    if (!req.file) return res.status(400).json({ error: "No DEM file uploaded" });
    if (!country || !state)
      return res.status(400).json({ error: "country and state are required. DEM upload is State or District level only." });

    // Safely move the file out of tmp into its final destination now that req.body is fully parsed
    const destDir = path.join(
      req.app.locals.DATA_ROOT,
      _safe(country),
      _safe(state),
      district ? _safe(district) : "_state_level",
      "dem_features"
    );

    // Garbage collect any bloated previous uploads securely
    if (fs.existsSync(destDir)) {
      fs.readdirSync(destDir).forEach(f => {
        const ext = path.extname(f).toLowerCase();
        if ([".tif", ".tiff", ".asc", ".img", ".zip"].includes(ext)) {
          try { fs.unlinkSync(path.join(destDir, f)); } catch (_) {}
        }
      });
    } else {
      fs.mkdirSync(destDir, { recursive: true });
    }

    const finalPath = path.join(destDir, req.file.filename);
    
    // Windows EBUSY FIX: Use Copy + Unlink instead of Rename if handles are locked
    try {
      fs.renameSync(req.file.path, finalPath);
    } catch (err) {
      if (err.code === 'EBUSY' || err.code === 'EPERM') {
        console.warn(`[Upload] Rename failed (busy), falling back to Copy-by-Move: ${req.file.path}`);
        fs.copyFileSync(req.file.path, finalPath);
        try { fs.unlinkSync(req.file.path); } catch (_) { 
          // If we can't unlink yet, let the OS cleanup temp later, don't block the user
        }
      } else {
        throw err;
      }
    }
    req.file.path = finalPath; // reassign so db and child scripts grab the accurate disk location

    // Find or create region
    let regionId;
    const existing = await pool.query(
      `SELECT id FROM regions
       WHERE country=$1
         AND (state=$2 OR (state IS NULL AND $2 IS NULL))
         AND (district=$3 OR (district IS NULL AND $3 IS NULL))`,
      [country, state || null, district || null]
    );

    if (existing.rows.length > 0) {
      regionId = existing.rows[0].id;
    } else {
      const { rows } = await pool.query(
        `INSERT INTO regions (country, state, district) VALUES ($1,$2,$3) RETURNING id`,
        [country, state || null, district || null]
      );
      regionId = rows[0].id;
      await pool.query(
        `INSERT INTO data_inventory (region_id) VALUES ($1) ON CONFLICT DO NOTHING`,
        [regionId]
      );
    }

    // Record DEM upload
    await pool.query(
      `INSERT INTO dem_uploads (region_id, file_path, file_name, file_size_mb)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (region_id) DO UPDATE
         SET file_path=$2, file_name=$3, file_size_mb=$4, uploaded_at=NOW()`,
      [regionId, req.file.path, req.file.originalname,
       Math.round(req.file.size / (1024 * 1024) * 100) / 100]
    );

    // Create job (DEM + terrain classification)
    const jobId = uuidv4();
    await pool.query(
      `INSERT INTO jobs (id, region_id, module, status, file_path, log)
       VALUES ($1,$2,'dem','pending',$3,'Job created — DEM uploaded, starting pipeline')`,
      [jobId, regionId, req.file.path]
    );

    // Kick off async GIS processing
    _triggerDemPipeline(req.app, jobId, regionId, req.file.path, country, state, district);

    res.status(202).json({
      jobId,
      regionId,
      message: "DEM accepted. Starting topo extraction + terrain classification ...",
      stages: ["Topographic Feature Extraction", "Terrain Classification"],
    });
  }
);

// ─── POST /api/upload (legacy – kept for compatibility) ───────────────────────
router.post(
  "/",
  protect,
  adminOnly,
  (req, res, next) => {
    // Redirect to module-specific endpoints
    const mod = req.body?.module;
    if (mod === "dem") {
      req.url = "/dem";
      return router.handle(req, res, next);
    }
    next();
  },
  upload.single("file"),
  async (req, res) => {
    const { country, state, district, module: mod } = req.body;
    if (!req.file) return res.status(400).json({ error: "No file uploaded" });

    let regionId;
    const existing = await pool.query(
      `SELECT id FROM regions WHERE country=$1 AND (state=$2 OR (state IS NULL AND $2 IS NULL))
       AND (district=$3 OR (district IS NULL AND $3 IS NULL))`,
      [country, state || null, district || null]
    );

    if (existing.rows.length > 0) {
      regionId = existing.rows[0].id;
    } else {
      const { rows } = await pool.query(
        `INSERT INTO regions (country, state, district) VALUES ($1,$2,$3) RETURNING id`,
        [country, state || null, district || null]
      );
      regionId = rows[0].id;
      await pool.query(`INSERT INTO data_inventory (region_id) VALUES ($1)`, [regionId]);
    }

    const jobId = uuidv4();
    await pool.query(
      `INSERT INTO jobs (id, region_id, module, status, file_path, log)
       VALUES ($1,$2,$3,'pending',$4,'Job created')`,
      [jobId, regionId, mod || "dem", req.file.path]
    );

    _triggerGenericProcessing(req.app, jobId, regionId, mod, req.file.path, country, state, district);

    res.status(202).json({ jobId, message: "Upload accepted. Processing started." });
  }
);

// ─── DEM + Terrain Pipeline ───────────────────────────────────────────────────
async function _triggerDemPipeline(app, jobId, regionId, filePath, country, state, district) {
  const GIS_URL  = process.env.GIS_SERVICE_URL || "http://localhost:8000";
  const broadcast = app.locals.broadcastJob;

  const _update = async (status, progress, log, terrain_stats = null) => {
    await pool.query(
      `UPDATE jobs SET status=$1, progress=$2, log=$3, updated_at=NOW() WHERE id=$4`,
      [status, progress, log, jobId]
    );
    broadcast(jobId, { status, progress, log, terrain_stats });
  };

  try {
    // --- STAGE 1: Topography ---
    await _update("processing", 10, "Stage 1: Starting Topographic Feature Extraction...");

    const topoRes = await axios.post(`${GIS_URL}/api/dem/process-dem`, {
      job_id: jobId, region_id: regionId, file_path: filePath,
      country, state, district, stage: "topo"
    }, { timeout: 60 * 60 * 1000 });

    const topoData = topoRes.data;
    
    // Update inventory for Stage 1
    await pool.query(
      `INSERT INTO topographic_features (region_id, output_dir, job_log)
       VALUES ($1, $2, $3)
       ON CONFLICT (region_id) DO UPDATE SET output_dir=$2, job_log=$3, generated_at=NOW()`,
      [regionId, topoData.output_dir, topoData.log || ""]
    );
    await pool.query(`UPDATE data_inventory SET dem_ready=TRUE, updated_at=NOW() WHERE region_id=$1`, [regionId]);

    // Report intermediate progress
    await _update("processing", 60, `✓ Stage 1 Complete: Topographic features generated.\n${topoData.log}\n\nStage 2: Starting Terrain Classification...`);

    // --- STAGE 2: Terrain Classification ---
    const terrainRes = await axios.post(`${GIS_URL}/api/dem/process-dem`, {
      job_id: jobId, region_id: regionId, file_path: filePath,
      country, state, district, stage: "terrain"
    }, { timeout: 60 * 60 * 1000 });

    const result = terrainRes.data;

    // Store terrain classification
    if (result.terrain) {
      const t = result.terrain;
      await pool.query(
        `INSERT INTO terrain_classifications
           (region_id, tif_path, shp_path, class_stats, dominant_class, dominant_name)
         VALUES ($1,$2,$3,$4,$5,$6)
         ON CONFLICT (region_id) DO UPDATE SET
           tif_path=$2, shp_path=$3, class_stats=$4,
           dominant_class=$5, dominant_name=$6, classified_at=NOW()`,
        [regionId, t.tif_path || null, t.shp_path || null,
         JSON.stringify(t.class_stats || {}),
         t.dominant_class || null, t.dominant_name || null]
      );
      await pool.query(`UPDATE data_inventory SET terrain_ready=TRUE, updated_at=NOW() WHERE region_id=$1`, [regionId]);
    }

    const finalLog = `✓ Pipeline Complete: DEM processed and Terrain classified.\n${topoData.log}\n\n${result.log}`;
    await _update("done", 100, finalLog, result.terrain?.class_stats);

  } catch (err) {
    const errMsg = err.response?.data?.detail || err.message || "GIS service error";
    await _update("failed", 0, `Pipeline failed: ${errMsg}`);
  }
}

// ─── Generic legacy handler ───────────────────────────────────────────────────
async function _triggerGenericProcessing(app, jobId, regionId, mod, filePath, country, state, district) {
  const GIS_URL  = process.env.GIS_SERVICE_URL || "http://localhost:8000";
  const broadcast = app.locals.broadcastJob;

  try {
    await pool.query(
      `UPDATE jobs SET status='processing', log='Sending to GIS service...', updated_at=NOW() WHERE id=$1`,
      [jobId]
    );
    broadcast(jobId, { status: "processing", progress: 10 });

    const epMap = { dem: "api/dem/process-dem", exposure: "api/dem/process-exposure", manual: "api/dem/process-manual" };
    const ep    = epMap[mod] || "api/dem/process-dem";

    const { data } = await axios.post(`${GIS_URL}/${ep}`, {
      job_id: jobId, region_id: regionId, file_path: filePath,
      country, state, district,
    }, { timeout: 30 * 60 * 1000 });

    await pool.query(
      `UPDATE jobs SET status='done', progress=100, log=$1, updated_at=NOW() WHERE id=$2`,
      [data.log || "Completed", jobId]
    );

    const invField = { dem: "dem_ready", exposure: "exposure_ready", manual: "manual_ready" }[mod];
    if (invField) {
      await pool.query(
        `UPDATE data_inventory SET ${invField}=TRUE, updated_at=NOW() WHERE region_id=$1`,
        [regionId]
      );
    }
    broadcast(jobId, { status: "done", progress: 100, log: data.log });
  } catch (err) {
    const msg = err.response?.data?.detail || err.message;
    await pool.query(
      `UPDATE jobs SET status='failed', log=$1, updated_at=NOW() WHERE id=$2`, [msg, jobId]
    );
    broadcast(jobId, { status: "failed", progress: 0, log: msg });
  }
}

module.exports = router;
