require("dotenv").config();
const { Pool } = require("pg");
const fs = require("fs");
const path = require("path");

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

async function migrate() {
  const migrationsDir = path.join(__dirname, "migrations");
  
  try {
    // 1. Ensure history table exists
    await pool.query(`
      CREATE TABLE IF NOT EXISTS migrations_history (
        id SERIAL PRIMARY KEY,
        file_name TEXT UNIQUE NOT NULL,
        applied_at TIMESTAMPTZ DEFAULT NOW()
      );
    `);

    // 2. Get all .sql files and sort them
    const files = fs.readdirSync(migrationsDir)
      .filter(f => f.endsWith(".sql"))
      .sort();

    // 3. Get already applied migrations
    const { rows: history } = await pool.query("SELECT file_name FROM migrations_history");
    const appliedFiles = new Set(history.map(h => h.file_name));

    console.log("==========================================");
    console.log("     Aether-Disaster Migration Engine     ");
    console.log("==========================================");

    let count = 0;
    for (const file of files) {
      if (appliedFiles.has(file)) {
        continue;
      }

      console.log(`\n🚀 Applying: ${file}...`);
      const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");

      // Use a transaction for each file
      const client = await pool.connect();
      try {
        await client.query("BEGIN");
        await client.query(sql);
        await client.query("INSERT INTO migrations_history (file_name) VALUES ($1)", [file]);
        await client.query("COMMIT");
        console.log(`✅ Success: ${file}`);
        count++;
      } catch (err) {
        await client.query("ROLLBACK");
        console.error(`❌ FAILED: ${file}`);
        console.error(`Error: ${err.message}`);
        process.exit(1);
      } finally {
        client.release();
      }
    }

    if (count === 0) {
      console.log("\n✨ System is up to date. No new migrations found.");
    } else {
      console.log(`\n🎉 Finished! Applied ${count} new migration(s).`);
    }

  } catch (err) {
    console.error("Migration engine error:", err);
    process.exit(1);
  } finally {
    await pool.end();
  }
}

// Run if called directly
if (require.main === module) {
  migrate();
}

module.exports = migrate;
