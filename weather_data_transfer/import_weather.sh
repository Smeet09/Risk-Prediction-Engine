#!/bin/bash

# Configuration
CONTAINER_NAME="prediction_db"
DB_NAME="prediction_engine"
DB_USER="postgres"
INPUT_FILE="weather_data_backup.sql"

echo "======================================================"
echo "   Weather Data Import & Sync (Linux)"
echo "======================================================"

# Check if file exists
if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: $INPUT_FILE not found in current directory!"
    exit 1
fi

echo "Importing $INPUT_FILE into $CONTAINER_NAME..."

# Copy file to container
docker cp ./"$INPUT_FILE" "$CONTAINER_NAME":/tmp/"$INPUT_FILE"

# Execute import
docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -f /tmp/"$INPUT_FILE"

echo "Syncing weather logs..."
docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "INSERT INTO jobs (module, status, country, state, log) SELECT DISTINCT 'weather', 'done', country, state, 'Restored from backup' FROM weather_data ON CONFLICT DO NOTHING;"

echo "======================================================"
echo "   SUCCESS: Data imported and Logs synced!"
echo "======================================================"
