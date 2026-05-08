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
    console.log("  Prediction Engine System Handover Cleaner ");
    console.log("==========================================");

    try {
        console.log("\n[1/3] Wiping operational data (preserving weather/manual indices)...");

        // 1. Wipe jobs EXCEPT weather and manual import history
        await pool.query(`DELETE FROM jobs WHERE module NOT IN ('weather', 'manual_import', 'manual_data')`);

        // 2. Wipe ONLY operational results (No Cascade on master tables)
        await pool.query(`
            TRUNCATE TABLE 
                rainfall_timeseries,
                susceptibility_results,
                dem_uploads,
                topographic_features,
                terrain_classifications,
                dynamic_risk_results,
                susceptibility_flood,
                susceptibility_landslide,
                dynamic_risk_flood,
                dynamic_risk_landslide
            RESTART IDENTITY;
        `);

        // 3. Reset Inventory but SELF-HEAL based on persisting tables (Weather & Manual)
        console.log("   -> Self-healing data inventory status...");

        // First reset local region flags
        await pool.query(`
            UPDATE data_inventory 
            SET dem_ready = FALSE, 
                terrain_ready = FALSE, 
                topo_ready = FALSE,
                susceptibility_ready = FALSE
        `);

        // Sync global manual readiness (if all 5 layers exist in manual_data_india)
        const { rows: manualCheck } = await pool.query(`SELECT COUNT(*)::int FROM manual_data_india`);
        if (manualCheck[0].count >= 5) {
            await pool.query(`UPDATE data_inventory SET manual_india_ready = TRUE`);
        }

        console.log("✅ Operational database tables cleaned (Weather & Manual logs preserved).");

        console.log("\n[2/3] Re-injecting default administrator account...");

        // Passwords for both below are 'Admin@1234' (hash matches the standard migration)
        const passwordHash = '$2a$10$92IXUNpkjO0rOQ5byMi.Ye4oKoEa3Ro9llC/.og/at2.uheWG/igi';

        await pool.query(`
            INSERT INTO users (email, password, role, full_name)
            VALUES 
                ('admin@bisag.predictionengine', $1, 'admin', 'System Administrator'),
                ('user@bisag.predictionengine', $1, 'user', 'Demo User')
            ON CONFLICT (email) DO NOTHING;
        `, [passwordHash]);

        console.log("✅ Default access accounts re-created.");

        console.log("\n[3/3] Preserving large master data boundaries...");
        console.log("✅ States, Districts, Talukas, and Villages boundary tables kept intact.");
        console.log("✅ India-wide Manual datasets (River, Fault, Coastal, LULC, Soil) kept intact.");

        console.log("\n🎉 Database cleanup complete. Everything is neat and clean for handover!");

    } catch (err) {
        console.error("❌ Error cleaning database:", err);
    } finally {
        await pool.end();
    }
}

cleanDatabase();
