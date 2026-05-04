# 🛠️ Risk Prediction Engine Setup Guide

This guide provides detailed instructions to set up and run the Risk Prediction Engine correctly on any machine. Follow these steps in order for a smooth installation.

---

## 📋 System Prerequisites

Ensure you have the following installed before proceeding:
1.  **Docker Desktop** (Latest Version): Crucial for PostGIS and n8n orchestration.
2.  **Node.js (v18 or v20 LTS)**: Required for the Unified Backend and Frontend.
3.  **Python (3.11 - 3.13)**: Required for the GIS Microservice and Smart Agents.
4.  **GDAL/WhiteboxTools**: (Optional) These are usually handled by the GIS service, but ensure your system allows binary execution.

---

## 🗄️ Step 1: Infrastructure (Docker)

The system uses **PostgreSQL 15** with **PostGIS** and **n8n**.

1.  **Ensure no other services** are using ports `5432` or `5678`.
2.  **Start the containers**:
    ```powershell
    # Try the new Docker Compose V2 command first
    docker compose up -d

    # If that fails, use the old V1 command
    docker-compose up -d
    ```
3.  **Create Admin Migration**: The database tables will auto-generate on first boot.

---

## 🔑 Step 2: Environment Config

Copy the example files in **all four** locations. **Do not skip this.**

```powershell
# 1. Root Folder
copy .env.example .env

# 2. Backend Folder
copy backend\.env.example backend\.env

# 3. GIS Service Folder
copy gis-service\.env.example gis-service\.env

# 4. Frontend Folder
copy frontend\.env.example frontend\.env
```

### 🌍 Critical Variables for Portability:
- **`DATA_ROOT`**: In the root `.env`, set this to your project's `database` path.
- **`CDS_KEY`**: Get your key from [Copernicus CDS](https://cds.climate.copernicus.eu/) and put it in `gis-service/.env`.

---

## 🚀 Step 3: Launching Services

Open **three** separate terminals. If you are on a new machine, **ALWAYS run `npm install` first**.

### Terminal A: Master Backend (Node.js)
```powershell
cd backend
npm install
npm run dev
```
- **Port**: `4000` (Must be free)

### Terminal B: GIS Microservice (Python)
```powershell
cd gis-service
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
- **Port**: `8000`

### Terminal C: Frontend Dashboard (React + Vite)
```powershell
cd frontend
npm install
npm run dev
```
- **Port**: `3001` or `3000`

---

## 🤖 Step 4: AI Agent Orchestration (n8n Sync)

The system is only "Smart" once n8n has the workflow injected.

1.  **Run the Auto-Sync Utility**:
    - **Windows**: `.\scripts\setup_n8n.bat`
    - **Linux/macOS**: `bash scripts/setup_n8n.sh`
2.  **Verify**: Open `http://localhost:5678` in your browser. 
3.  **Activate**: Click on the workflow named "Risk Prediction Engine Architecture" and toggle it to **ACTIVE** in the top right corner.

---

## 🛠️ Step 5: Troubleshooting (Common Glitches)

| Issue | Solution |
| :--- | :--- |
| **"Pipeline Running" stuck in UI** | Click the **"Repair & Sync"** button in the Admin Dashboard. |
| **CORS / Access Denied** | Ensure `backend/server.js` lists your frontend port (3000/3001) in the allow list. |
| **n8n Status Offline** | Ensure Docker is running and you have run `setup_n8n.bat`. |
| **No Active Regions Found** | You must generate a **Susceptibility Map** for a region before the dynamic agent can map it. |
| **Python: Module Not Found** | Ensure you are inside the `venv` and ran `pip install -r requirements.txt`. |

---

## 🌍 Portability & Data Export

To move the system to a new PC:
1.  Zip the entire folder (including `Dataset/`).
2.  Move it to the new PC.
3.  Update the **`DATA_ROOT`** in your `.env` to the new absolute path.
4.  Follow **Steps 1 to 4** above.
