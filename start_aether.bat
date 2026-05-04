@echo off
echo ==============================================
echo       PREDICTION ENGINE SYSTEM
echo                 STARTUP SCRIPT
echo ==============================================
echo.

echo [1/3] Starting Backend Server...
start "Prediction Engine Backend" cmd /k "cd backend && npm run dev"

echo [2/3] Starting Frontend Request...
start "Prediction Engine Frontend" cmd /k "cd frontend && npm run dev"

echo [3/3] Starting Python GIS Service...
start "Prediction Engine GIS Microservice" cmd /k "cd gis-service && .\venv\Scripts\activate && uvicorn main:app --reload --port 8000"

echo.
echo All services are launching in separate windows!
echo DO NOT CLOSE those terminal windows to keep the system running.
echo To stop the system, exit out of each of the individual command prompt windows.
pause
