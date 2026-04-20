@echo off
echo 🚀 Exporting Database Data...

:: Ensure we are in the project root
cd /d "%~dp0.."

:: Check if container is running
docker ps --filter "name=aether_db" --format "{{.Names}}" | findstr "aether_db" >nul
if %errorlevel% neq 0 (
    echo ❌ Error: The database container 'aether_db' is not running.
    echo Please run 'docker-compose up -d' first.
    pause
    exit /b 1
)

:: Export data
echo 📦 Generating dump to database/data_dump.sql...
docker exec -i aether_db pg_dump -U aether aether_disaster > database/data_dump.sql

if %errorlevel% equ 0 (
    echo ✅ Export complete! You can now share 'database/data_dump.sql' with your team.
) else (
    echo ❌ Export failed.
)

pause
