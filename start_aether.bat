@echo off
echo ==============================================
echo       AETHER DISASTER PREDICTION SYSTEM
echo                 STARTUP SCRIPT
echo ==============================================
echo.

echo [1/3] Starting Backend Server...
start "Aether Backend" cmd /k "cd backend && npm run dev"

echo [2/3] Starting Frontend Request...
start "Aether Frontend" cmd /k "cd frontend && npm run dev"

echo [3/3] Starting Python GIS Service...
start "Aether GIS Microservice" cmd /k "cd gis-service && .\venv\Scripts\activate && uvicorn main:app --reload --port 8000"

echo.
echo All services are launching in separate windows!
echo DO NOT CLOSE those terminal windows to keep the system running.
echo To stop the system, exit out of each of the individual command prompt windows.
pause
