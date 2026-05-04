@echo off
echo ==============================================
echo       PREDICTION ENGINE SYSTEM
echo                 STARTUP SCRIPT
echo ==============================================
echo.
echo Cleaning up existing ports to avoid conflicts...
call npx kill-port 4000 3000 3001 8000 >nul 2>&1
echo.

echo [1/4] Starting Backend Server...
start "Prediction Engine Backend" cmd /k "cd backend && npm run dev"

echo [2/4] Starting Frontend Request...
start "Prediction Engine Frontend" cmd /k "cd frontend && npm run dev"

echo [3/4] Starting Python GIS Service...
start "Prediction Engine GIS Microservice" cmd /k "cd gis-service && .\venv\Scripts\activate && uvicorn main:app --reload --port 8000"

echo [4/4] Starting Crop Prediction (Streamlit)...
start "Crop Prediction" cmd /k "cd \"Crop Prediction\" && streamlit run main_app.py --server.port 8501"

echo.
echo All services are launching in separate windows!
echo DO NOT CLOSE those terminal windows to keep the system running.
echo To stop the system, exit out of each of the individual command prompt windows.
pause
