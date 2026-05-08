@echo off
SET CONTAINER_NAME=prediction_db
SET DB_NAME=prediction_engine
SET DB_USER=postgres
SET TABLE_NAME=weather_data
SET OUTPUT_FILE=weather_data_backup.sql

echo Exporting %TABLE_NAME% from %CONTAINER_NAME%...
docker exec %CONTAINER_NAME% pg_dump -U %DB_USER% -d %DB_NAME% -t %TABLE_NAME% -f /tmp/%OUTPUT_FILE%
docker cp %CONTAINER_NAME%:/tmp/%OUTPUT_FILE% ./%OUTPUT_FILE%

echo Export complete: %OUTPUT_FILE%
pause
