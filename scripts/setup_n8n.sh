#!/bin/bash

# ============================================================
# AI Agent Orchestration — Auto-Setup Utility (Linux/macOS)
# ============================================================

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

# 2. Check if aether_n8n container exists
echo "[2/3] Checking n8n Container..."
if [ $(docker ps -q -f name=aether_n8n | wc -l) -eq 0 ]; then
    echo "[INFO] n8n container not found or stopped. Attempting to start..."
    docker-compose up -d n8n
    echo "[WAIT] Waiting 10 seconds for n8n to initialize..."
    sleep 10
fi

# 3. Export/Import Workflow
echo "[3/3] Importing Agent Workflow into n8n..."

# Copy workflow file into container temp space
docker cp n8n/workflow.json aether_n8n:/tmp/workflow.json

# Use n8n CLI to import it
docker exec -it aether_n8n n8n import:workflow --input=/tmp/workflow.json

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
