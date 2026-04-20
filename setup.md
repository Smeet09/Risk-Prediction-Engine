# 🛠️ Risk Prediction Engine Setup Guide

This guide provides detailed instructions to set up and run the Risk Prediction Engine on any machine.

---

## 📋 System Prerequisites

Ensure you have the following installed:
1.  **Docker Desktop**: Required for the spatial database.
2.  **Node.js (v18+)**: For the Backend and Frontend services.
3.  **Python (3.11 - 3.13)**: For the GIS Microservice.
4.  **GDAL/WhiteboxTools**: (Optional, but required for production processing). WhiteboxTools is typically downloaded automatically by the GIS service or should be placed in the library path.

---

## 🗄️ 1. Database Setup

The system uses **PostgreSQL 15** with **PostGIS**.

1.  **Start the container**:
    ```powershell
    docker-compose up -d
    ```
2.  **Initialize Schema (if needed)**:
    If the automatic migrations don't run or you want a fresh start, use the provided schema:
    ```powershell
    docker exec -i aether_db psql -U aether -d aether_disaster < database/schema.sql
    ```

---

## 🔑 2. Environment Configuration

The system relies on `.env` files for configuration. Copy the example files and update them with your local paths.

```powershell
# Root (Used by Docker)
copy .env.example .env

# Backend
copy backend\.env.example backend\.env

# GIS Service
copy gis-service\.env.example gis-service\.env

# Frontend
copy frontend\.env.example frontend\.env
```

### Critical Environment Variables:
- `DATA_ROOT`: (In `.env`) Absolute path to your project's storage directory.
- `DATABASE_URL`: Connection string for PostgreSQL.
- `GEE_SERVICE_ACCOUNT`: (Optional) For Earth Engine weather downloads.

---

## 🚀 3. Starting the Services

Open three separate terminals to run each component.

### Terminal A: Backend API (Node.js)
```powershell
cd backend
npm install
npm run dev
```
- **Port**: `5000`

### Terminal B: GIS Microservice (Python)
```powershell
cd gis-service
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
- **Port**: `8000`
- **Docs**: `http://localhost:8000/docs`

### Terminal C: Frontend UI (React + Vite)
```powershell
cd frontend
npm install
npm run dev
```
- **Port**: `3001` (or as shown in terminal)

---

---

## 🌍 4. Portability & Moving Data

The system is designed to be mobile.
- **Move the folder**: You can move the entire project folder to any location.
- **Update Paths**: Simply edit the `DATA_ROOT` in your `.env` file to point to the new absolute path.
- **Data Persistence**: All GIS datasets and uploads are stored in the directory defined by `DATA_ROOT`.

---

## 🔄 5. Syncing Database Data

If you want to transfer your local database state (all tables and data) to another machine:

### A. On your machine (Export)
1.  Ensure the database container is running (`docker-compose up -d`).
2.  Run the export script:
    ```powershell
    .\scripts\export_db.bat
    ```
3.  This creates a file `database\data_dump.sql`.
4.  Share this file with your team member (via Google Drive, Slack, etc.).

### B. On team member's machine (Import)
1.  Place the `data_dump.sql` file into the `database/` folder.
2.  Ensure the database container is running (`docker-compose up -d`).
3.  Run the import script:
    ```powershell
    .\scripts\import_db.bat
    ```
    > [!WARNING]
    > This will overwrite any existing local data in the team member's database.

---

## 🛠️ 6. Utility Tools

Check the `scripts/` and `backend/tools/` directories for administrative scripts:
- `clean_database.js`: Resets system metadata.
- `terrain_classify_india.py`: Logic for large-scale terrain analysis.
- `weather_gee_csv_script.py`: Extracts rainfall data from GEE.
