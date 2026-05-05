@echo off
setlocal enabledelayedexpansion

:: ============================================================
:: AI Agent Orchestration — Auto-Setup Utility (Windows)
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."
set "WORKFLOW_FILE=%ROOT_DIR%\n8n\workflow.json"
set "WORKFLOW_NAME=Risk Prediction Engine Architecture"

echo ============================================================
echo AI Agent Orchestration -- Auto-Setup Utility
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

:: 2. Check if prediction_n8n container exists
echo [2/3] Checking n8n Container...
docker ps --filter "name=prediction_n8n" --format "{{.Names}}" | findstr /i "prediction_n8n" >nul
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] n8n container not found. Starting services...
    :: Try Docker Compose V2 first, then fall back to V1
    docker compose version >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        docker compose -f "%ROOT_DIR%\docker-compose.yml" up -d n8n
    ) else (
        docker-compose -f "%ROOT_DIR%\docker-compose.yml" up -d n8n
    )
    echo [WAIT] Waiting 10 seconds for n8n to initialize...
    timeout /t 10 /nobreak >nul
)

:: 3. Export/Import Workflow
echo [3/3] Importing Agent Workflow into n8n...

if not exist "%WORKFLOW_FILE%" (
    echo [ERROR] Workflow file not found at %WORKFLOW_FILE%
    pause
    exit /b 1
)

:: Check if workflow already exists (idempotent behavior)
docker exec prediction_n8n n8n export:workflow --all > "%TEMP%\n8n_workflows.json" 2>nul
if %ERRORLEVEL% EQU 0 (
    findstr /c:"%WORKFLOW_NAME%" "%TEMP%\n8n_workflows.json" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [INFO] Workflow already exists in n8n. Skipping import.
        del /f /q "%TEMP%\n8n_workflows.json" 2>nul
        echo.
        echo [SUCCESS] AI Agent Pipeline is already configured!
        echo [INFO] Open http://localhost:5678 and ensure the workflow is ACTIVE.
        echo.
        echo ============================================================
        pause
        exit /b 0
    )
)
del /f /q "%TEMP%\n8n_workflows.json" 2>nul

:: Inject a UUID into the workflow JSON using PowerShell (idempotent import)
set "TMP_WORKFLOW=%TEMP%\workflow_import.json"
powershell -NoProfile -Command ^
    "$wf = Get-Content '%WORKFLOW_FILE%' -Raw | ConvertFrom-Json; " ^
    "if (-not $wf.id) { $wf | Add-Member -NotePropertyName 'id' -NotePropertyValue ([guid]::NewGuid().ToString()); } " ^
    "$wf | ConvertTo-Json -Depth 100 | Set-Content '%TMP_WORKFLOW%'"

:: Copy workflow into container and import
docker cp "%TMP_WORKFLOW%" prediction_n8n:/tmp/workflow.json
del /f /q "%TMP_WORKFLOW%" 2>nul

docker exec prediction_n8n n8n import:workflow --input=/tmp/workflow.json

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [SUCCESS] AI Agent Pipeline has been successfully injected!
    echo [INFO] Open http://localhost:5678 and ensure the workflow is ACTIVE.
) else (
    echo.
    echo [ERROR] Failed to import workflow. Please ensure n8n container is healthy.
)

echo.
echo ============================================================
pause
