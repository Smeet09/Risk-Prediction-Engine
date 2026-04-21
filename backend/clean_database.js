const { Pool } = require('pg');
const dotenv = require('dotenv');
const path = require('path');

// Load environment variables from the same directory (backend/.env)
dotenv.config({ path: path.join(__dirname, '.env') });

const pool = new Pool({
    connectionString: process.env.DATABASE_URL,
});

async function cleanDatabase() {
    console.log("==========================================");
    console.log("  Aether-Disaster System Handover Cleaner ");
    console.log("==========================================");

    try {
        console.log("\n[1/3] Wiping all operational data...");
        // TRUNCATE empties the tables but keeps schema.
        // CASCADE ensures dependent tables empty as well.
        // We explicitly EXCLUDE boundary files so they remain available for mapping.
        await pool.query(`
            TRUNCATE TABLE 
                jobs, 
                data_inventory, 
                rainfall_timeseries,
                susceptibility_results,
                dem_uploads,
                topographic_features,
                terrain_classifications,
                weather_data,
                regions,
                users,
                susceptibility_flood,
                susceptibility_landslide
            RESTART IDENTITY CASCADE;
        `);
        console.log("✅ Operational database tables cleaned.");

        console.log("\n[2/3] Re-injecting default administrator account...");

        // Passwords for both below are 'Admin@1234' (hash matches the standard migration)
        const passwordHash = '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi';

        await pool.query(`
            INSERT INTO users (email, password, role, full_name)
            VALUES 
                ('admin@aether.local', $1, 'admin', 'System Administrator'),
                ('user@aether.local', $1, 'user', 'Demo User')
            ON CONFLICT (email) DO NOTHING;
        `, [passwordHash]);

        console.log("✅ Default access accounts re-created.");

        console.log("\n[3/3] Preserving large master data boundaries...");
        console.log("✅ States, Districts, Talukas, and Villages boundary tables kept intact.");

        console.log("\n🎉 Database cleanup complete. Everything is neat and clean for handover!");

    } catch (err) {
        console.error("❌ Error cleaning database:", err);
    } finally {
        await pool.end();
    }
}

cleanDatabase();
