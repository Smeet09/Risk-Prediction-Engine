# 🤖 Agentic AI Architecture — Risk Prediction Engine
> **Goal**: Evolve the system into a fully autonomous, multi-agent pipeline where a Master Orchestrator manages specialized sub-agents. Zero human interaction required after initial setup.

---

## 🧠 The Big Picture

The system has 1 Orchestrator Agent that controls 5 specialized sub-agents:

```
[Orchestrator Agent]
    ├── [Data Ingestion Agent]  → fetches ERA5, MODIS/Sentinel, soil data
    ├── [GIS Processing Agent]  → terrain analysis, susceptibility maps
    ├── [ML Prediction Agent]   → flood/landslide risk scoring
    ├── [Alert & Reporting Agent] → PDFs, email, Slack alerts
    └── [Crop Intelligence Agent] → crop suitability predictions
```

### Agent Flow (Sequence)
```
Every 6 hours (CRON):
  1. Orchestrator → triggers Data Ingestion Agent
  2. On DATA_READY → Orchestrator triggers GIS Agent + Crop Agent (parallel)
  3. On GIS_READY  → Orchestrator triggers ML Agent
  4. On ML_READY   → Orchestrator triggers Alert Agent
  5. Alert Agent sends reports → Orchestrator logs run as SUCCESS
  6. Any failure   → retry 3x with backoff, then log FAILED + notify
```

---

## 📋 Agent Definitions

### Agent 0: Orchestrator Agent (Master Controller)
- **Tech**: Node.js  
- **Location**: `backend/agents/orchestrator/`  
- **Trigger**: Cron schedule (every 6 hours) OR event callback from any sub-agent  
- **Responsibilities**:
  - Maintains global pipeline state machine
  - Decides which agents to trigger and in what order
  - Handles failures and retries (3x exponential backoff)
  - Logs all agent actions to PostgreSQL `agent_logs` table
  - Broadcasts real-time status to Frontend via WebSocket

---

### Agent 1: Data Ingestion Agent
- **Tech**: Python + FastAPI  
- **Location**: `gis-service/agents/data_ingestion_agent.py`  
- **Trigger**: HTTP POST from Orchestrator  
- **Responsibilities**:
  - Auto-fetch ERA5 rainfall data (CDSAPI)
  - Poll and download MODIS/Sentinel satellite imagery
  - Check for new soil/geology layer updates
  - Update PostGIS tables with fresh data
  - POST callback to Orchestrator with DATA_READY payload

---

### Agent 2: GIS Processing Agent
- **Tech**: Python + FastAPI + WhiteboxTools + GDAL  
- **Location**: `gis-service/agents/gis_agent.py`  
- **Trigger**: HTTP POST from Orchestrator after DATA_READY  
- **Responsibilities**:
  - Run terrain analysis (slope, TWI, curvature) on fresh data
  - Generate flood + landslide susceptibility .tif maps
  - Store output rasters in `database/output/`
  - Update job metadata in PostgreSQL
  - POST callback GIS_READY to Orchestrator

---

### Agent 3: ML Prediction Agent
- **Tech**: Python (scikit-learn, TensorFlow/PyTorch)  
- **Location**: `gis-service/agents/ml_agent.py`  
- **Trigger**: HTTP POST from Orchestrator after GIS_READY  
- **Responsibilities**:
  - Run logistic regression + DL ensemble for flood/landslide
  - Generate dynamic risk scores per district
  - Run time-series forecast (24h/48h/72h risk windows)
  - Save prediction results to PostgreSQL `predictions` table
  - POST callback PREDICTIONS_READY to Orchestrator

---

### Agent 4: Alert & Reporting Agent
- **Tech**: Node.js + Nodemailer + Puppeteer (PDF)  
- **Location**: `backend/agents/alert_agent/`  
- **Trigger**: HTTP POST from Orchestrator after PREDICTIONS_READY  
- **Responsibilities**:
  - Compare risk score against configurable thresholds
  - Generate PDF susceptibility reports per region
  - Send email/webhook alerts for HIGH/CRITICAL risk areas
  - Post summary to Slack/Teams webhook (optional)
  - POST callback ALERTS_DISPATCHED to Orchestrator

---

### Agent 5: Crop Intelligence Agent
- **Tech**: Python  
- **Location**: `Crop Prediction/agents/crop_agent.py`  
- **Trigger**: HTTP POST from Orchestrator (runs parallel with GIS Agent)  
- **Responsibilities**:
  - Auto-fetch latest weather/soil for crop zones
  - Run crop suitability prediction models
  - Generate crop risk layer
  - Store output in PostgreSQL
  - POST callback CROP_READY to Orchestrator

---

## 🔄 n8n Workflow Design Reference

n8n is the visual no-code orchestration layer on top. The workflow nodes:

```
[Cron Trigger: Every 6h]
        ↓
[HTTP Request Node: POST /agents/data-ingestion/run]
        ↓
[IF Node: status == "success"]
   YES → [Parallel Split Node]
           ├── [HTTP Request: POST /agents/gis/run]
           └── [HTTP Request: POST /agents/crop/run]
                      ↓ (Merge/Wait Node)
           [HTTP Request: POST /agents/ml/run]
                      ↓
           [HTTP Request: POST /agents/alerts/run]
                      ↓
           [Postgres Node: INSERT agent_run_log]
   NO  → [Slack/Email Node: Notify failure]
          [Postgres Node: INSERT error_log]
          [Wait Node: 30min → back to top with retry counter]
```

### n8n Nodes Needed:
| Node | Purpose |
|------|---------|
| Cron | Schedule pipeline every 6h |
| HTTP Request | Call each agent REST endpoint |
| IF / Switch | Route based on agent status |
| Merge/Wait | Synchronize parallel branches |
| Postgres | Log runs to database |
| Email / Slack | Send alerts on failure or high risk |
| Function | Custom JS for payload building |
| Error Trigger | Global catch for unhandled failures |
| Webhook | Receive agent callbacks |

---

## 🏗️ Implementation Plan

### Phase 1 — Agent Infrastructure (Week 1)
- [ ] Create `backend/agents/orchestrator/` with state machine
- [ ] Create `gis-service/agents/` directory structure
- [ ] Add `agent_runs` and `agent_logs` PostgreSQL tables
- [ ] Implement retry + exponential backoff in Orchestrator
- [ ] Add `/agents/callback` endpoint on Orchestrator

### Phase 2 — Sub-Agent APIs (Week 2)
- [ ] Wrap existing GIS scripts into `gis_agent.py` FastAPI endpoint
- [ ] Wrap ML models into `ml_agent.py` endpoint
- [ ] Create `data_ingestion_agent.py` with ERA5/CDSAPI fetching
- [ ] Create `alert_agent/` with PDF + email dispatch
- [ ] Create `crop_agent.py` wrapping existing models

### Phase 3 — n8n Integration (Week 2-3)
- [ ] Add n8n service to `docker-compose.yml`
- [ ] Import `n8n/workflow.json` into n8n instance
- [ ] Configure Cron triggers, HTTP nodes, and Postgres logging
- [ ] End-to-end autonomous pipeline test

### Phase 4 — Monitoring Dashboard (Week 3)
- [ ] Frontend: New "Agent Pipeline" page
- [ ] Real-time WebSocket feed of agent statuses
- [ ] Pipeline DAG visualization with live node states
- [ ] History log of all agent runs with timing metrics

---

## 🗄️ Database Schema Additions

```sql
-- Pipeline run tracking
CREATE TABLE agent_runs (
  id           SERIAL PRIMARY KEY,
  run_id       UUID DEFAULT gen_random_uuid(),
  triggered_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ,
  status       VARCHAR(20) CHECK (status IN ('running','success','failed','partial')),
  regions      TEXT[],
  data_date    DATE
);

-- Per-agent step logs
CREATE TABLE agent_logs (
  id             SERIAL PRIMARY KEY,
  run_id         UUID REFERENCES agent_runs(run_id),
  agent_name     VARCHAR(50),
  started_at     TIMESTAMPTZ DEFAULT NOW(),
  ended_at       TIMESTAMPTZ,
  status         VARCHAR(20),
  attempt_num    INT DEFAULT 1,
  output_payload JSONB,
  error_message  TEXT
);

-- Alert dispatch history
CREATE TABLE agent_alerts (
  id                SERIAL PRIMARY KEY,
  run_id            UUID,
  region            VARCHAR(100),
  disaster_type     VARCHAR(50),
  risk_level        VARCHAR(20),
  risk_score        FLOAT,
  alert_sent_at     TIMESTAMPTZ DEFAULT NOW(),
  channels_notified TEXT[]
);
```

---

## 📁 Directory Structure

```
Prediction Engine/
├── backend/
│   └── agents/
│       ├── orchestrator/
│       │   ├── index.js          <- Main orchestrator HTTP server
│       │   ├── state_machine.js  <- Agent state transitions
│       │   └── retry.js          <- Retry + backoff logic
│       └── alert_agent/
│           ├── index.js          <- Alert dispatcher entry
│           ├── pdf_generator.js  <- Puppeteer PDF report builder
│           └── notifier.js       <- Email / Slack / Webhook sender
│
├── gis-service/
│   └── agents/
│       ├── data_ingestion_agent.py  <- ERA5 + satellite auto-fetcher
│       ├── gis_agent.py             <- Terrain analysis + susceptibility
│       ├── ml_agent.py              <- Flood/landslide ML runner
│       └── agent_utils.py           <- Shared callback + logging helpers
│
├── Crop Prediction/
│   └── agents/
│       └── crop_agent.py        <- Crop model runner
│
└── n8n/
    └── workflow.json            <- n8n importable workflow
```

---

## 🔌 Standard Agent API Contract

Every agent exposes the same interface:

### POST /agents/{name}/run
```json
// REQUEST (Orchestrator to Agent)
{
  "run_id": "uuid-1234",
  "region": "Gujarat",
  "disaster_type": "flood",
  "data_date": "2026-04-30",
  "callback_url": "http://localhost:3001/agents/callback",
  "config": {}
}

// RESPONSE (immediate acknowledgment)
{
  "accepted": true,
  "run_id": "uuid-1234",
  "agent": "gis",
  "estimated_duration_seconds": 120
}
```

### POST /agents/callback (Agent to Orchestrator when done)
```json
{
  "run_id": "uuid-1234",
  "agent": "gis",
  "status": "success",
  "outputs": {
    "tif_path": "/database/output/flood_Gujarat_2026-04-30.tif",
    "job_id": 42
  },
  "duration_seconds": 95
}
```

---

## 🔒 Zero-Human Interaction Guarantees

| Scenario | Autonomous Handling |
|----------|-------------------|
| Data fetch failure | Retry 3x with backoff, skip region on final fail |
| GIS processing crash | Agent restarts, logs error, continues other regions |
| ML model divergence | Fallback to previous day's predictions |
| High risk detected | Auto PDF + send alert, no human approval needed |
| New region added to DB | Orchestrator auto-includes in next scheduled run |
| Server restart | Orchestrator resumes from last agent_logs checkpoint |
| n8n failure | Error Trigger node catches + notifies + retries |

> **The only required human action**: One-time `.env` config (API keys for ERA5, SMTP, Slack).  
> After that: 100% autonomous, 24/7.

---

## 🚀 Quick Start: Add n8n to Docker Compose

Add to `docker-compose.yml`:

```yaml
  n8n:
    image: n8nio/n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=admin123
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - WEBHOOK_URL=http://localhost:5678/
    volumes:
      - n8n_data:/home/node/.n8n

volumes:
  n8n_data:
```

Run: `docker-compose up -d n8n` then open `http://localhost:5678`

---

## ✅ Recommended First Steps

Given your current running system:

1. **Build Orchestrator** (`backend/agents/orchestrator/index.js`) — state machine + cron
2. **Wrap GIS pipeline** into `gis-service/agents/gis_agent.py` FastAPI endpoint
3. **Add n8n to Docker** — connect Cron → Orchestrator → GIS Agent loop
4. **Add DB tables** for `agent_runs` + `agent_logs`
5. **Frontend**: Add a simple "Pipeline Status" card to the Admin Dashboard

This gives a working autonomous GIS loop in ~1 week, then add more agents incrementally.
