@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0"
:: Remove trailing backslash
if "%ROOT_DIR:~-1%"=="\" set "ROOT_DIR=%ROOT_DIR:~0,-1%"

echo ==============================================
echo       PREDICTION ENGINE SYSTEM
echo                 STARTUP SCRIPT
echo ==============================================
echo.

:: Kill any processes holding the required ports (matches lsof approach in .sh)
echo Cleaning up existing ports to avoid conflicts...
for %%P in (4000 3000 3001 8000 8501) do (
    for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%%P " ^| findstr "LISTENING" 2^>nul') do (
        taskkill /PID %%a /F >nul 2>&1
    )
)
echo.

:: Run n8n workflow sync (matches setup_n8n.sh call in .sh)
echo Initializing n8n workflow sync...
call "%ROOT_DIR%\scripts\setup_n8n.bat" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] n8n setup encountered an issue, continuing startup...
)
echo.

echo [1/4] Starting Backend Server...
start "Prediction Engine Backend" cmd /k "cd /d "%ROOT_DIR%\backend" && npm run dev"

echo [2/4] Starting Frontend...
start "Prediction Engine Frontend" cmd /k "cd /d "%ROOT_DIR%\frontend" && npm run dev"

echo [3/4] Starting Python GIS Service...
start "Prediction Engine GIS Microservice" cmd /k "cd /d "%ROOT_DIR%\gis-service" && .\venv\Scripts\activate && uvicorn main:app --reload --port 8000"

echo [4/4] Starting Crop Prediction (Streamlit)...
start "Crop Prediction" cmd /k "cd /d "%ROOT_DIR%\Crop Prediction" && streamlit run main_app.py --server.port 8501"

echo.
echo All services are launching in separate windows!
echo.
echo   - Backend API   : http://localhost:4000
echo   - Frontend UI   : http://localhost:3000 (or 3001)
echo   - GIS Service   : http://localhost:8000
echo   - Crop Predict  : http://localhost:8501
echo.
echo DO NOT CLOSE those terminal windows to keep the system running.
echo To stop the system, close each of the individual terminal windows.
pause
