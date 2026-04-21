require("dotenv").config();
const express = require("express");
const cors    = require("cors");
const path    = require("path");
const fs      = require("fs");
const http    = require("http");
const WebSocket = require("ws");
require("express-async-errors");

const app    = express();
const server = http.createServer(app);

// ─── WebSocket for real-time job updates ─────────────────────────────────────
const wss = new WebSocket.Server({ server, path: "/ws" });
const clients = new Map();

wss.on("connection", (ws, req) => {
  const jobId = new URL(req.url, "ws://localhost").searchParams.get("job");
  if (jobId) {
    if (!clients.has(jobId)) clients.set(jobId, new Set());
    clients.get(jobId).add(ws);
    ws.on("close", () => {
      clients.get(jobId)?.delete(ws);
      if (clients.get(jobId)?.size === 0) clients.delete(jobId);
    });
  }
});

app.locals.broadcastJob = (jobId, payload) => {
  const sockets = clients.get(jobId);
  if (!sockets) return;
  const msg = JSON.stringify(payload);
  sockets.forEach(ws => { if (ws.readyState === WebSocket.OPEN) ws.send(msg); });
};

// ─── Data root ────────────────────────────────────────────────────────────────
const DATA_ROOT = path.resolve(
  process.env.DATA_ROOT || path.join(__dirname, "..", "database")
);
if (!fs.existsSync(DATA_ROOT)) fs.mkdirSync(DATA_ROOT, { recursive: true });
app.locals.DATA_ROOT = DATA_ROOT;

// ─── Middleware ───────────────────────────────────────────────────────────────
app.use(cors({
  origin: process.env.FRONTEND_URL || "http://localhost:5173",
  credentials: true,
}));
app.use(express.json({ limit: "50mb" }));
app.use(express.urlencoded({ extended: true, limit: "50mb" }));

// ─── Routes ───────────────────────────────────────────────────────────────────
app.use("/api/auth",            require("./routes/auth"));
app.use("/api/regions",         require("./routes/regions"));
app.use("/api/upload",          require("./routes/upload"));
app.use("/api/jobs",            require("./routes/jobs"));
app.use("/api/rainfall",        require("./routes/rainfall"));
app.use("/api/susceptibility",  require("./routes/susceptibility"));
app.use("/api/boundaries",      require("./routes/boundaries"));
app.use("/api/manual",          require("./routes/manual").router);
app.use("/api/disasters",       require("./routes/disasters"));
app.use("/api/weather",         require("./routes/weather"));
app.use("/api/dynamic",         require("./routes/dynamic"));

// ─── Health check ─────────────────────────────────────────────────────────────
app.get("/health", (_req, res) => res.json({
  status:   "ok",
  version:  "2.0.0",
  dataRoot: DATA_ROOT,
}));

// ─── Global error handler ─────────────────────────────────────────────────────
app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(err.status || 500).json({ error: err.message || "Internal Server Error" });
});

const PORT = process.env.PORT || 4000;
server.listen(PORT, () => {
  console.log(`\n🌍 Aether Backend v2.0 running at http://localhost:${PORT}`);
  console.log(`📁 Data root: ${DATA_ROOT}`);
  console.log(`🔌 WebSocket: ws://localhost:${PORT}/ws`);
  console.log(`🗺️  Boundaries: /api/boundaries`);
  console.log(`📊 Disasters:  /api/disasters\n`);
});
