@echo off
echo 🚀 Importing Database Data...

:: Ensure we are in the project root
cd /d "%~dp0.."

if not exist "database\data_dump.sql" (
    echo ❌ Error: 'database\data_dump.sql' not found.
    echo Please make sure the file is in the database folder.
    pause
    exit /b 1
)

:: Check if container is running
docker ps --filter "name=prediction_db" --format "{{.Names}}" | findstr "prediction_db" >nul
if %errorlevel% neq 0 (
    echo ❌ Error: The database container 'prediction_db' is not running.
    echo Please run 'docker-compose up -d' first.
    pause
    exit /b 1
)

:: Confirm with user
echo ⚠️  WARNING: This will overwrite your local database data.
set /p confirm="Are you sure you want to proceed? (y/n): "
if /i "%confirm%" neq "y" (
    echo ❌ Import cancelled.
    pause
    exit /b 0
)

:: Drop and recreate public schema to ensure a clean import
echo 🧹 Cleaning local database...
docker exec -i prediction_db psql -U postgres -d prediction_engine -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public; CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"

:: Import data
echo 📥 Importing 'database/data_dump.sql'...
docker exec -i prediction_db psql -U postgres -d prediction_engine < database/data_dump.sql

if %errorlevel% equ 0 (
    echo ✅ Import complete! Your database is now synced.
) else (
    echo ❌ Import failed. Check the error messages above.
)

pause
