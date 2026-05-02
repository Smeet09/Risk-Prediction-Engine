@echo off
echo ============================================================
echo AI Agent Orchestration — Auto-Setup Utility
echo ============================================================
echo.

:: 1. Check if Docker is running
echo [1/3] Checking Docker Status...
docker ps >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

:: 2. Check if aether_n8n container exists
echo [2/3] Checking n8n Container...
docker ps --filter "name=aether_n8n" --format "{{.Names}}" | findstr /i "aether_n8n" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] n8n container not found. Starting services via docker-compose...
    docker-compose up -d n8n
    echo [WAIT] Waiting 10 seconds for n8n to initialize database...
    timeout /t 10 /nobreak >nul
)

:: 3. Export/Import Workflow
echo [3/3] Importing Agent Workflow into n8n...
:: Copy workflow file into container temp space
docker cp n8n/workflow.json aether_n8n:/tmp/workflow.json

:: Use n8n CLI to import it
docker exec -it aether_n8n n8n import:workflow --input=/tmp/workflow.json

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [SUCCESS] AI Agent Pipeline has been successfully injected!
    echo [INFO] You can now open n8n at http://localhost:5678 and see the workflow.
    echo [INFO] Don't forget to click 'Active' in n8n if it isn't enabled.
) else (
    echo.
    echo [ERROR] Failed to import workflow. Please ensure n8n container is healthy.
)

echo.
echo ============================================================
pause
