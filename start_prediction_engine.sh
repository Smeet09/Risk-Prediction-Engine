#!/bin/bash
echo "=============================================="
echo "      PREDICTION ENGINE SYSTEM"
echo "                STARTUP SCRIPT"
echo "=============================================="
echo ""

echo "Cleaning up existing ports to avoid conflicts..."
npx kill-port 4000 3000 3001 8000 8501 >/dev/null 2>&1
echo ""

echo "[1/4] Starting Backend Server..."
(cd backend && npm run dev) &
BACKEND_PID=$!

echo "[2/4] Starting Frontend Request..."
(cd frontend && npm run dev) &
FRONTEND_PID=$!

echo "[3/4] Starting Python GIS Service..."
(cd gis-service && source venv/bin/activate && uvicorn main:app --reload --port 8000) &
GIS_PID=$!

echo "[4/4] Starting Crop Prediction (Streamlit)..."
(cd "Crop Prediction" && streamlit run main_app.py --server.port 8501) &
CROP_PID=$!

echo ""
echo "All services are running in the background!"
echo ""
echo "  - Backend API   : http://localhost:4000"
echo "  - Frontend UI   : http://localhost:3000 (or 3001)"
echo "  - GIS Service   : http://localhost:8000"
echo "  - Crop Predict  : http://localhost:8501"
echo ""
echo "Press Ctrl+C to gracefully stop the entire system."

# Trap Ctrl+C to kill all child processes
trap "echo -e '\nStopping all services...'; kill $BACKEND_PID $FRONTEND_PID $GIS_PID $CROP_PID 2>/dev/null; exit 0" SIGINT SIGTERM

# Keep the script alive
wait

