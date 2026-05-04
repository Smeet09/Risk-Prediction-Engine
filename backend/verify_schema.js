const { Pool } = require('pg');
const pool = new Pool({ connectionString: 'postgres://postgres:postgres@localhost:5432/prediction_engine' });
pool.query("SELECT * FROM susceptibility_results LIMIT 1", (err, res) => {
    if (err) {
        console.error(err);
    } else {
        console.log(Object.keys(res.rows[0] || {}));
    }
    process.exit();
});
