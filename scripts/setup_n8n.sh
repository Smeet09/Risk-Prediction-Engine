#!/bin/bash

# ============================================================
# AI Agent Orchestration — Auto-Setup Utility (Linux/macOS)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKFLOW_FILE="$ROOT_DIR/n8n/workflow.json"
WORKFLOW_NAME="Risk Prediction Engine Architecture"

echo "============================================================"
echo "AI Agent Orchestration — Auto-Setup Utility"
echo "============================================================"
echo ""

# 1. Check if Docker is running
echo "[1/3] Checking Docker Status..."
if ! docker ps > /dev/null 2>&1; then
    echo "[ERROR] Docker is not running or you don't have permissions. Please start Docker."
    exit 1
fi

# 2. Check if prediction_n8n container exists
echo "[2/3] Checking n8n Container..."
if [ $(docker ps -q -f name=prediction_n8n | wc -l) -eq 0 ]; then
    echo "[INFO] n8n container not found or stopped. Attempting to start..."
    if docker compose version > /dev/null 2>&1; then
        docker compose -f "$ROOT_DIR/docker-compose.yml" up -d n8n
    else
        docker-compose -f "$ROOT_DIR/docker-compose.yml" up -d n8n
    fi
    echo "[WAIT] Waiting 10 seconds for n8n to initialize..."
    sleep 10
fi

# 3. Export/Import Workflow
echo "[3/3] Importing Agent Workflow into n8n..."

if [ ! -f "$WORKFLOW_FILE" ]; then
    echo "[ERROR] Workflow file not found at $WORKFLOW_FILE"
    exit 1
fi

# Skip import if workflow is already present (idempotent behavior)
if docker exec prediction_n8n n8n export:workflow --all > /tmp/n8n_workflows.json 2>/dev/null; then
    if grep -q "\"name\": \"$WORKFLOW_NAME\"" /tmp/n8n_workflows.json; then
        echo "[INFO] Workflow already exists in n8n. Skipping import."
        rm -f /tmp/n8n_workflows.json
        echo ""
        echo "[SUCCESS] AI Agent Pipeline is already configured!"
        echo "[INFO] Open http://localhost:5678 and ensure the workflow is ACTIVE."
        echo ""
        echo "============================================================"
        exit 0
    fi
fi
rm -f /tmp/n8n_workflows.json

# n8n import expects an id in JSON; inject a generated UUID into a temp file
WORKFLOW_ID="$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen)"
TMP_WORKFLOW="$(mktemp)"
awk -v wf_id="$WORKFLOW_ID" 'NR==1 { print; print "  \"id\": \"" wf_id "\","; next } { print }' "$WORKFLOW_FILE" > "$TMP_WORKFLOW"

# Copy workflow file into container temp space
docker cp "$TMP_WORKFLOW" prediction_n8n:/tmp/workflow.json
rm -f "$TMP_WORKFLOW"

# Use n8n CLI to import it
docker exec prediction_n8n n8n import:workflow --input=/tmp/workflow.json

if [ $? -eq 0 ]; then
    echo ""
    echo "[SUCCESS] AI Agent Pipeline has been successfully injected!"
    echo "[INFO] Open http://localhost:5678 and ensure the workflow is ACTIVE."
else
    echo ""
    echo "[ERROR] Failed to import workflow. Ensure n8n container is healthy."
fi

echo ""
echo "============================================================"