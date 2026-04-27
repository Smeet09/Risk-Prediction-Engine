const { Pool } = require('pg');
const dotenv = require('dotenv');
const path = require('path');

// Load environment variables from the same directory (backend/.env)
dotenv.config({ path: path.join(__dirname, '.env') });

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
});

async function repairWeatherLogs() {
    console.log("==========================================");
    console.log("  Aether-Disaster Weather Log Repair Tool ");
    console.log("==========================================");

    try {
        console.log("\n[1/2] Scanning weather_data table for actual records...");
        
        // Use the correct column 'date', 'state', and 'country' from the weather_data table
        const { rows: weatherRecords } = await pool.query(`
            SELECT DISTINCT 
                state, 
                country, 
                EXTRACT(YEAR FROM date)::int as year,
                EXTRACT(MONTH FROM date)::int as month
            FROM weather_data
        `);

        if (weatherRecords.length === 0) {
            console.log("ℹ️ No weather data found in the database. Nothing to repair.");
            return;
        }

        console.log(`[FOUND] ${weatherRecords.length} unique datasets in storage.`);

        console.log("\n[2/2] Restoring missing logs to the jobs table...");
        let repairedCount = 0;

        for (const record of weatherRecords) {
            const { state, country, year, month } = record;
            
            // Check if a 'done' log entry already exists to avoid duplicates
            const { rows: existing } = await pool.query(
                `SELECT id FROM jobs 
                 WHERE module='weather' AND state=$1 AND country=$2 AND year=$3 AND month=$4`,
                [state, country, year, month]
            );

            if (existing.length === 0) {
                console.log(` ✅ Restoring: ${state} (${month}/${year})`);
                await pool.query(
                    `INSERT INTO jobs (module, status, country, state, year, month, progress, log, created_at, updated_at)
                     VALUES ('weather', 'done', $1, $2, $3, $4, 100, 'Manually recovered from database records.', NOW(), NOW())`,
                    [country, state, year, month]
                );
                repairedCount++;
            }
        }

        console.log(`\n🎉 Success! Restored ${repairedCount} weather logs.`);
        console.log("You can now see these in the 'Weather Data Status Tracker' in the Admin Panel.");

    } catch (err) {
        console.error("❌ Error repairing logs:", err.message);
    } finally {
        await pool.end();
    }
}

repairWeatherLogs();
