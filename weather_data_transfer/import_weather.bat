@echo off
SET CONTAINER_NAME=prediction_db
SET DB_NAME=prediction_engine
SET DB_USER=postgres
SET INPUT_FILE=weather_data_backup.sql

echo Importing %INPUT_FILE% into %CONTAINER_NAME%...
echo Note: This will create/update the weather_data table.

:: Copy file to container
docker cp ./%INPUT_FILE% %CONTAINER_NAME%:/tmp/%INPUT_FILE%

:: Execute import
docker exec -i %CONTAINER_NAME% psql -U %DB_USER% -d %DB_NAME% -f /tmp/%INPUT_FILE%

echo Syncing weather logs...
docker exec -i %CONTAINER_NAME% psql -U %DB_USER% -d %DB_NAME% -c "INSERT INTO jobs (module, status, country, state, log) SELECT DISTINCT 'weather', 'done', country, state, 'Restored from backup' FROM weather_data ON CONFLICT DO NOTHING;"

echo Import and Sync complete.
pause
