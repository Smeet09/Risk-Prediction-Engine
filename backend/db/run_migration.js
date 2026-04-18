/**
 * run_migration.js — Apply a SQL migration file using the Node pg driver
 * Usage: node run_migration.js <sql-file>
 */
require("dotenv").config();
const { Pool } = require("pg");
const fs = require("fs");
const path = require("path");

const sqlFile = process.argv[2];
if (!sqlFile) {
  console.error("Usage: node run_migration.js <sql-file>");
  process.exit(1);
}

const sqlPath = path.resolve(sqlFile);
if (!fs.existsSync(sqlPath)) {
  console.error("File not found:", sqlPath);
  process.exit(1);
}

const sql = fs.readFileSync(sqlPath, "utf-8");

const pool = new Pool({ connectionString: process.env.DATABASE_URL });

(async () => {
  console.log(`\nRunning migration: ${sqlPath}\n`);
  try {
    await pool.query(sql);
    console.log("✅ Migration applied successfully.\n");
  } catch (err) {
    console.error("❌ Migration failed:", err.message);
    process.exit(1);
  } finally {
    await pool.end();
  }
})();
