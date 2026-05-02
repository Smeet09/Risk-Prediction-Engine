const express = require("express");
const router = express.Router();
const pool = require("../config/db");
const { protect } = require("../middleware/auth");
const axios = require("axios");
const { v4: uuidv4 } = require("uuid");

// ─── POST /api/agents/run ──────────────────────────────────────────────────────────
// Called by ORCHESTRATOR (or n8n) to start the whole pipeline or a specific agent
router.post("/run", async (req, res) => {
  const { regions, data_date } = req.body;
  // In a real system, you'd specify which agent or trigger 'all'
  
  const runId = uuidv4();
  try {
    await pool.query(
      `INSERT INTO agent_runs (run_id, triggered_by, status, regions, data_date) 
       VALUES ($1, 'api', 'running', $2, $3)`,
      [runId, regions || [], data_date || new Date().toISOString().split('T')[0]]
    );

    // Initial log
    await pool.query(
      `INSERT INTO agent_logs (run_id, agent_name, agent_label, status, current_step)
       VALUES ($1, 'orchestrator', 'Master Initialization', 'success', 'Pipeline Registered')`,
      [runId]
    );

    // Call n8n Webhook to physically start the workflow
    try {
      await axios.post("http://localhost:5678/webhook/run-pipeline", { run_id: runId });
    } catch (n8nErr) {
      console.warn("Could not trigger n8n Webhook (is n8n running and workflow active?)");
    }

    res.status(202).json({
      message: "Agent pipeline initialized",
      run_id: runId
    });

  } catch (err) {
    console.error("[AgentRun Error]:", err);
    res.status(500).json({ error: err.message });
  }
});

// ─── POST /api/agents/logs ─────────────────────────────────────────────────────────
// Sub-agents hit this to log their step-by-step progress
router.post("/logs", async (req, res) => {
  let { run_id, agent_name, agent_label, status, progress_pct, current_step, payload, error } = req.body;

  try {
    // Resolve global errors from n8n where run_id might be lost in the crash trace
    if (run_id === "LATEST") {
      const { rows } = await pool.query(
        "SELECT run_id FROM agent_runs WHERE status='running' ORDER BY triggered_at DESC LIMIT 1"
      );
      if (rows.length > 0) {
        run_id = rows[0].run_id;
      } else {
        return res.status(404).json({ error: "No running jobs found to fail" });
      }
    }

    // Upsert equivalent logic for the particular step
    await pool.query(
      `INSERT INTO agent_logs (run_id, agent_name, agent_label, status, progress_pct, current_step, output_payload, error_message, ended_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, CASE WHEN $4 IN ('success', 'failed') THEN NOW() ELSE NULL END)`,
       [run_id, agent_name, agent_label, status, progress_pct || 0, current_step, JSON.stringify(payload || {}), error]
    );

    // If failed, maybe mark the whole run as failed
    if (status === 'failed') {
      await pool.query(`UPDATE agent_runs SET status='failed', completed_at=NOW() WHERE run_id=$1`, [run_id]);
    }

    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/agents/health-check ──────────────────────────────────────────────────
router.get("/health-check", protect, async (req, res) => {
  try {
    // 1. Check n8n connectivity
    let n8nStatus = "offline";
    try {
      // Try to head the n8n portal
      await axios.get("http://localhost:5678", { timeout: 2000 });
      n8nStatus = "online";
    } catch (e) {
      n8nStatus = "offline";
    }

    // 2. Check if there are active runs
    const { rows: activeRuns } = await pool.query("SELECT count(*) FROM agent_runs WHERE status='running'");
    
    res.json({
      n8n: n8nStatus,
      active_runs: parseInt(activeRuns[0].count),
      system: "healthy"
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── POST /api/agents/sync ─────────────────────────────────────────────────────────
// Force-clears stale jobs and ensures system is ready for a new run
router.post("/sync", protect, async (req, res) => {
  try {
    // 1. Mark all 'running' jobs as 'failed' (stale cleanup)
    await pool.query("UPDATE agent_runs SET status='failed', completed_at=NOW() WHERE status='running'");
    
    // 2. Add a system log informing about the sync
    const syncId = uuidv4();
    await pool.query(
      `INSERT INTO agent_runs (run_id, triggered_by, status, regions) 
       VALUES ($1, 'system_sync', 'success', $2)`,
      [syncId, []]
    );
    await pool.query(
      `INSERT INTO agent_logs (run_id, agent_name, agent_label, status, current_step)
       VALUES ($1, 'orchestrator', 'Manual Sync', 'success', 'System State Reset and Synchronized')`,
      [syncId]
    );

    res.json({ message: "System synchronized and stale runs cleared." });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── GET /api/agents/history ───────────────────────────────────────────────────────
// Fetches the dashboard stats
router.get("/history", protect, async (req, res) => {
  try {
    const { rows: runs } = await pool.query(
      `SELECT * FROM agent_runs ORDER BY triggered_at DESC LIMIT 10`
    );

    // Get logs for the latest run
    let latestLogs = [];
    if (runs.length > 0) {
      const latestRunId = runs[0].run_id;
      const logRes = await pool.query(`SELECT * FROM agent_logs WHERE run_id=$1 ORDER BY started_at ASC`, [latestRunId]);
      latestLogs = logRes.rows;
    }

    // Get alerts history
    const { rows: alerts } = await pool.query(
      `SELECT * FROM agent_alerts ORDER BY alert_sent_at DESC LIMIT 5`
    );

    res.json({
      runs,
      latest_logs: latestLogs,
      alerts
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

module.exports = router;
