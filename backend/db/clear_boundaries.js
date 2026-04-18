require("dotenv").config();
const { Pool } = require("pg");
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

(async () => {
  try {
    console.log("Truncating spatial boundaries tables...");
    await pool.query("TRUNCATE states_boundaries, districts_boundaries, talukas_boundaries, villages_boundaries RESTART IDENTITY CASCADE;");
    console.log("✅ Tables are completely wiped clean.");
  } catch(e) {
    console.error("Error wiping:", e.message);
  } finally {
    pool.end();
  }
})();
