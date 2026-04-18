require("dotenv").config();
const { Pool } = require("pg");

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

(async () => {
  try {
    console.log("Cleaning old dummy regions...");
    await pool.query("DELETE FROM jobs;");
    await pool.query("DELETE FROM data_inventory;");
    await pool.query("DELETE FROM susceptibility_results;");
    await pool.query("DELETE FROM terrain_classifications;");
    await pool.query("DELETE FROM topographic_features;");
    await pool.query("DELETE FROM dem_uploads;");
    await pool.query("DELETE FROM regions;"); // cascade will handle most, but just to be sure
    console.log("✅ Dummy regions cleared.");
  } catch (err) {
    console.error("Error cleaning:", err);
  } finally {
    pool.end();
  }
})();
