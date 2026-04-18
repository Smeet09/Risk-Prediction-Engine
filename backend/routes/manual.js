const express = require("express");
const router  = express.Router();
const multer  = require("multer");
const path    = require("path");
const fs      = require("fs");
const pool    = require("../config/db");
const { protect, adminOnly } = require("../middleware/auth");
const axios   = require("axios");

// ─── Multer — India-wide manual data ──────────────────────────────────────────
const storage = multer.diskStorage({
  destination: (req, _file, cb) => {
    const DATA_ROOT = req.app.locals.DATA_ROOT;
    const dest = path.join(DATA_ROOT, "manual_data_india", req.body.data_type || "misc");
    fs.mkdirSync(dest, { recursive: true });
    cb(null, dest);
  },
  filename: (_req, file, cb) => {
    cb(null, `${Date.now()}_${file.originalname}`);
  },
});

const upload = multer({
  storage,
  limits: { fileSize: 2048 * 1024 * 1024 }, // 2 GB for large India-wide datasets
});

const VALID_TYPES = ["lulc", "river", "soil", "fault", "coastline"];

// ─── GET /api/manual ──────────────────────────────────────────────────────────
// List all India-wide manual datasets with live table verification
router.get("/", protect, async (_req, res) => {
  const { rows } = await pool.query(
    `SELECT m.*, u.full_name AS uploaded_by_name
     FROM manual_data_india m
     LEFT JOIN users u ON u.id = m.uploaded_by
     ORDER BY m.data_type`
  );

  // Live verification: Check if PostGIS tables actually exist for vector layers
  const verifiedRows = await Promise.all(rows.map(async (row) => {
    if (["river", "fault", "coastline"].includes(row.data_type)) {
      const tablePresent = await _isTablePresent(row.data_type);
      // Override postgis_imported if table is missing
      if (!tablePresent && row.postgis_imported) {
        row.postgis_imported = false;
        // Optional: Sync back to DB if you want to persist the "lost" status
        // await pool.query("UPDATE manual_data_india SET postgis_imported = FALSE WHERE data_type = $1", [row.data_type]);
      }
    }
    return row;
  }));

  res.json({ datasets: verifiedRows });
});

/**
 * Helper to check if a PostGIS table exists for a given data type.
 */
async function _isTablePresent(dataType) {
  const tableMap = {
    river: "india_rivers",
    fault: "india_faults",
    coastline: "india_coastlines"
  };
  const tableName = tableMap[dataType];
  if (!tableName) return true; // Non-vector types (LULC, Soil) are "always present" via file

  try {
    const { rows } = await pool.query(
      `SELECT EXISTS (
         SELECT FROM information_schema.tables 
         WHERE table_schema = 'public' 
         AND table_name = $1
       )`,
      [tableName]
    );
    return rows[0].exists;
  } catch (err) {
    console.error(`Check table existence failed for ${tableName}:`, err.message);
    return false;
  }
}

// ─── POST /api/manual ─────────────────────────────────────────────────────────
// Upload / replace one India-wide dataset type (admin only)
router.post(
  "/",
  protect,
  adminOnly,
  upload.single("file"),
  async (req, res) => {
    const { data_type, description } = req.body;
    if (!req.file) return res.status(400).json({ error: "No file uploaded" });
    if (!VALID_TYPES.includes(data_type))
      return res.status(400).json({ error: `data_type must be one of: ${VALID_TYPES.join(", ")}` });

    // Remove old file if being replaced
    const existing = await pool.query(
      `SELECT file_path FROM manual_data_india WHERE data_type=$1`, [data_type]
    );
    if (existing.rows.length > 0) {
      const oldPath = existing.rows[0].file_path;
      if (oldPath && fs.existsSync(oldPath)) {
        try { fs.unlinkSync(oldPath); } catch (_) {}
      }
    }

    // Verify user ID exists in DB to avoid FK constraint issues from stale tokens
    const userId = req.user?.id;
    const userCheck = userId ? await pool.query("SELECT id FROM users WHERE id=$1", [userId]) : { rows: [] };
    const finalUserId = userCheck.rows.length > 0 ? userId : null;

    const { rows } = await pool.query(
      `INSERT INTO manual_data_india (data_type, file_path, file_name, description, uploaded_by)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (data_type) DO UPDATE SET
         file_path   = EXCLUDED.file_path,
         file_name   = EXCLUDED.file_name,
         description = EXCLUDED.description,
         uploaded_by = EXCLUDED.uploaded_by,
         uploaded_at = NOW()
       RETURNING *`,
      [data_type, req.file.path, req.file.originalname, description || null, finalUserId]
    );

    // Wait for the triggerPostGisImport below to handle the final inventory update
    // We removed the premature update here as it was causing the "Fake Ready" bug.

    // Trigger PostGIS import for any of the 5 manual types
    // The GIS service handles vector vs raster logic (rasters just return success)
    let postgisStatus = null;
    if (VALID_TYPES.includes(data_type)) {
      try {
        const importResult = await _triggerPostGisImport(data_type, rows[0].file_path);
        postgisStatus = importResult.message;
      } catch (err) {
        postgisStatus = "PostGIS processing failed: " + err.message;
      }
    }

    res.status(201).json({ 
      dataset: rows[0], 
      message: postgisStatus 
        ? `${data_type} uploaded and indexed: ${postgisStatus}`
        : `${data_type} uploaded successfully`,
      postgisStatus
    });
  }
);

// ─── POST /api/manual/register-path ──────────────────────────────────────────
// Register an existing local file path (for large LULC)
router.post(
  "/register-path",
  protect,
  adminOnly,
  async (req, res) => {
    const { data_type, local_path, description } = req.body;
    if (!data_type || !local_path) 
      return res.status(400).json({ error: "data_type and local_path are required" });
    if (!fs.existsSync(local_path))
      return res.status(400).json({ error: "File path does not exist on disk" });

    // Verify user ID exists in DB to avoid FK constraint issues from stale tokens
    const userId = req.user?.id;
    const userCheck = userId ? await pool.query("SELECT id FROM users WHERE id=$1", [userId]) : { rows: [] };
    const finalUserId = userCheck.rows.length > 0 ? userId : null;

    const { rows } = await pool.query(
      `INSERT INTO manual_data_india (data_type, file_path, file_name, description, uploaded_by, is_local_path)
       VALUES ($1, $2, $3, $4, $5, TRUE)
       ON CONFLICT (data_type) DO UPDATE SET
         file_path   = EXCLUDED.file_path,
         file_name   = EXCLUDED.file_name,
         description = EXCLUDED.description,
         uploaded_by = EXCLUDED.uploaded_by,
         is_local_path = TRUE,
         uploaded_at = NOW()
       RETURNING *`,
      [data_type, local_path, path.basename(local_path), description || null, finalUserId]
    );

    res.status(201).json({ dataset: rows[0], message: `${data_type} path registered successfully` });
  }
);

async function _triggerPostGisImport(dataType, filePath) {
  const GIS_URL = process.env.GIS_SERVICE_URL || "http://localhost:8000";
  try {
    const response = await axios.post(`${GIS_URL}/api/india-layers/import-vector`, {
      data_type: dataType,
      file_path: filePath
    });
    
    await pool.query(
      `UPDATE manual_data_india SET postgis_imported = TRUE WHERE data_type = $1`,
      [dataType]
    );
    
    // Sync the global status after successful import
    await _syncGlobalManualReadiness();
    
    return { success: true, message: response.data.message };
  } catch (err) {
    const errMsg = err.response?.data?.detail || err.message;
    console.error(`PostGIS import failed for ${dataType}:`, errMsg);
    throw new Error(errMsg);
  }
}

/**
 * Ensures data_inventory.manual_india_ready is only TRUE if:
 * 1. All 5 layers are uploaded.
 * 2. 3/3 spatial layers (river, fault, coastline) have postgis_imported = TRUE.
 */
async function _syncGlobalManualReadiness() {
  const VALID_TYPES = ["lulc", "river", "soil", "fault", "coastline"];
  const VECTOR_TYPES = ["river", "fault", "coastline"];

  const { rows } = await pool.query(
    `SELECT data_type, postgis_imported FROM manual_data_india WHERE data_type = ANY($1)`,
    [VALID_TYPES]
  );

  // Check counts
  const typesPresent = rows.map(r => r.data_type);
  const allPresent = VALID_TYPES.every(t => typesPresent.includes(t));
  
  if (!allPresent) {
    await pool.query("UPDATE data_inventory SET manual_india_ready = FALSE, updated_at = NOW()");
    return;
  }

  // Check spatial imports with live verification
  let vectorsReady = true;
  for (const row of rows) {
    if (VECTOR_TYPES.includes(row.data_type)) {
      if (!row.postgis_imported) {
        vectorsReady = false;
        break;
      }
      // Live check: table MUST exist in DB
      const tablePresent = await _isTablePresent(row.data_type);
      if (!tablePresent) {
        vectorsReady = false;
        break;
      }
    }
  }

  const isFullyReady = allPresent && vectorsReady;

  await pool.query(
    `UPDATE data_inventory SET manual_india_ready = $1, updated_at = NOW()`,
    [isFullyReady]
  );
  console.log(`[StatusSync] India-wide manual status updated to: ${isFullyReady}`);
}

// ─── DELETE /api/manual/:type ─────────────────────────────────────────────────
router.delete("/:type", protect, adminOnly, async (req, res) => {
  const { type } = req.params;
  if (!VALID_TYPES.includes(type))
    return res.status(400).json({ error: "Invalid data type" });

  const { rows } = await pool.query(
    `DELETE FROM manual_data_india WHERE data_type=$1 RETURNING file_path`, [type]
  );
  if (rows.length === 0)
    return res.status(404).json({ error: "Dataset not found" });

  const filePath = rows[0].file_path;
  if (filePath && fs.existsSync(filePath)) {
    try { fs.unlinkSync(filePath); } catch (_) {}
  }

  // Mark manual_india_ready as false and sync
  await _syncGlobalManualReadiness();

  res.json({ message: `${type} dataset removed` });
});

/**
 * Computes the real-time global readiness status for India-wide manual data.
 * Checks for all 5 layers + PostGIS table existence.
 */
async function getGlobalManualReadiness() {
  const VECTOR_TYPES = ["river", "fault", "coastline"];

  const { rows } = await pool.query(
    `SELECT data_type, postgis_imported FROM manual_data_india WHERE data_type = ANY($1)`,
    [VALID_TYPES]
  );

  // Check counts
  const typesPresent = rows.map(r => r.data_type);
  const allPresent = VALID_TYPES.every(t => typesPresent.includes(t));
  if (!allPresent) return false;

  // Check spatial imports with live verification
  for (const row of rows) {
    if (VECTOR_TYPES.includes(row.data_type)) {
      if (!row.postgis_imported) return false;
      
      const tablePresent = await _isTablePresent(row.data_type);
      if (!tablePresent) return false;
    }
  }

  return true;
}

module.exports = {
  router,
  VALID_TYPES,
  getGlobalManualReadiness,
  _isTablePresent
};
