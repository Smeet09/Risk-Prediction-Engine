-- ============================================================
-- 011_agent_tables.sql
-- Agentic AI pipeline tables: runs, per-agent logs, alerts
-- ============================================================

-- Master pipeline run record (one per cron tick or manual trigger)
CREATE TABLE IF NOT EXISTS agent_runs (
  id            SERIAL PRIMARY KEY,
  run_id        UUID DEFAULT gen_random_uuid() UNIQUE NOT NULL,
  triggered_by  VARCHAR(50) DEFAULT 'cron',      -- 'cron' | 'manual' | 'api'
  triggered_at  TIMESTAMPTZ DEFAULT NOW(),
  completed_at  TIMESTAMPTZ,
  status        VARCHAR(20) DEFAULT 'running'
                CHECK (status IN ('running','success','failed','partial')),
  regions       TEXT[],
  data_date     DATE DEFAULT CURRENT_DATE,
  total_agents  INT DEFAULT 5,
  done_agents   INT DEFAULT 0
);

-- Per-agent step log (one row per agent per run)
CREATE TABLE IF NOT EXISTS agent_logs (
  id              SERIAL PRIMARY KEY,
  run_id          UUID NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
  agent_name      VARCHAR(50) NOT NULL,   -- 'data_ingestion'|'gis'|'ml'|'alert'|'crop'
  agent_label     VARCHAR(100),
  started_at      TIMESTAMPTZ DEFAULT NOW(),
  ended_at        TIMESTAMPTZ,
  status          VARCHAR(20) DEFAULT 'pending'
                  CHECK (status IN ('pending','running','success','failed','skipped')),
  attempt_num     INT DEFAULT 1,
  progress_pct    INT DEFAULT 0,
  current_step    TEXT,
  output_payload  JSONB,
  error_message   TEXT
);

-- Alert dispatch history
CREATE TABLE IF NOT EXISTS agent_alerts (
  id                SERIAL PRIMARY KEY,
  run_id            UUID,
  region            VARCHAR(100),
  disaster_type     VARCHAR(50),
  risk_level        VARCHAR(20),           -- 'low'|'moderate'|'high'|'critical'
  risk_score        FLOAT,
  alert_sent_at     TIMESTAMPTZ DEFAULT NOW(),
  channels_notified TEXT[],
  report_path       TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_agent_runs_status     ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_triggered  ON agent_runs(triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_logs_run_id     ON agent_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_agent      ON agent_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_alerts_run_id   ON agent_alerts(run_id);
