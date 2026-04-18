import { useState, useEffect, useCallback } from "react";
import { getStates, getDistricts, uploadDem, getJob } from "../../lib/api";

const TERRAIN_CLASSES = {
  1: { name: "Coastal Lowland",    color: "#0096c7" },
  2: { name: "Floodplain",         color: "#48cae4" },
  3: { name: "Alluvial Plain",     color: "#90e0ef" },
  4: { name: "Valley / River Basin", color: "#2dc653" },
  5: { name: "Piedmont / Foothill", color: "#80b918" },
  6: { name: "Low Hill",           color: "#ffd60a" },
  7: { name: "High Hill",          color: "#f4a261" },
  8: { name: "Mountain",           color: "#e63946" },
  9: { name: "Plateau / Mesa",     color: "#9b5de5" },
  10:{ name: "Escarpment / Cliff", color: "#c1121f" },
  11:{ name: "Arid Plain",         color: "#d4a373" },
  12:{ name: "Coastal Dune",       color: "#06d6a0" },
};

const STAGES = [
  { id: "upload",   label: "DEM Upload",                     pct: 10  },
  { id: "topo",     label: "Topographic Feature Extraction",  pct: 60  },
  { id: "terrain",  label: "Terrain Classification",          pct: 95  },
  { id: "complete", label: "Complete",                        pct: 100 },
];

export default function DemUpload() {
  const [states,    setStates]    = useState([]);
  const [districts, setDistricts] = useState([]);

  const [form, setForm] = useState({ state: "", district: "" });
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);

  // Pipeline state
  const [jobId,       setJobId]       = useState(null);
  const [jobStatus,   setJobStatus]   = useState(null); // pending|processing|done|failed
  const [progress,    setProgress]    = useState(0);
  const [log,         setLog]         = useState("");
  const [terrainStats, setTerrainStats] = useState(null);
  const [uploadPct,   setUploadPct]   = useState(0);
  const [error,       setError]       = useState("");
  const [loading,     setLoading]     = useState(false);

  // Load states on mount and poll
  useEffect(() => {
    const fetchS = () => {
      getStates().then(d => {
        setStates(prev => JSON.stringify(prev) !== JSON.stringify(d.states) ? (d.states || []) : prev);
      }).catch(() => {});
    };
    fetchS();
    const iv = setInterval(fetchS, 3000);
    return () => clearInterval(iv);
  }, []);

  // Load districts when state changes
  useEffect(() => {
    if (!form.state) { setDistricts([]); return; }
    getDistricts(form.state)
      .then(d => setDistricts(d.districts || []))
      .catch(() => setDistricts([]));
    setForm(f => ({ ...f, district: "" }));
  }, [form.state]);

  // WebSocket for real-time progress
  const connectWs = useCallback((id) => {
    const wsBase = (window.location.protocol === "https:" ? "wss:" : "ws:")
      + "//" + window.location.host;
    const ws = new WebSocket(`${wsBase}/ws?job=${id}`);
    ws.onmessage = e => {
      const data = JSON.parse(e.data);
      setProgress(data.progress ?? 0);
      setLog(data.log ?? "");
      setJobStatus(data.status);
      if (data.status === "done" || data.status === "failed") ws.close();
    };
    return ws;
  }, []);

  // Poll job status (fallback when WS unavailable)
  const pollJob = useCallback((id) => {
    const iv = setInterval(async () => {
      try {
        const d = await getJob(id);
        const j = d.job;
        setProgress(j.progress ?? 0);
        setLog(j.log ?? "");
        setJobStatus(j.status);
        if (j.status === "done" || j.status === "failed") clearInterval(iv);
      } catch (_) {}
    }, 3000);
    return iv;
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (!form.state) return setError("Please select a state.");
    if (!file)       return setError("Please select a DEM file.");

    try {
      // PROACTIVELY CHECK FOR DUPLICATION (Real-world safeguard)
      const r_res = await fetch(`${window.location.protocol}//${window.location.host}/api/regions/flat`, {
        headers: { Authorization: `Bearer ${localStorage.getItem("aether_token")}` }
      });
      if (r_res.ok) {
        const d = await r_res.json();
        const existing = d.regions?.find(r => r.state === form.state && (r.district || "") === (form.district || ""));
        if (existing && existing.dem_ready) {
          const proceed = window.confirm(
            `⚠️ OVERWRITE WARNING\n\nTopographic and Terrain Classification data already physically exists for ${form.district || form.state}.\n\nUploading a new DEM will irrecoverably delete and overwrite the existing outputs on the server and update the Database. Do you wish to proceed?`
          );
          if (!proceed) return;
        }
      }
    } catch (_) {
      // Ignore network errors on preemptive check
    }

    setLoading(true);
    setJobStatus("uploading");
    setProgress(2);
    setLog("Uploading DEM file...");
    setTerrainStats(null);

    try {
      const fd = new FormData();
      fd.append("country",  "India");
      fd.append("state",    form.state);
      if (form.district) fd.append("district", form.district);
      fd.append("file",     file);

      const res = await uploadDem(fd, pct => setUploadPct(pct));

      setJobId(res.jobId);
      setJobStatus("processing");
      setProgress(10);
      setLog("DEM accepted. Starting topographic extraction...");

      // Connect WebSocket and poll as fallback
      const ws = connectWs(res.jobId);
      const iv = pollJob(res.jobId);

      // When job done, fetch terrain stats from job log or a follow-up GET
      ws.onmessage = e => {
        const data = JSON.parse(e.data);
        setProgress(data.progress ?? 0);
        setLog(data.log ?? "");
        setJobStatus(data.status);
        if (data.terrain_stats) setTerrainStats(data.terrain_stats);
        
        if (data.status === "done") {
          ws.close();
          clearInterval(iv);
          _fetchTerrainStats(res.jobId);
          // PHYSICAL NOTIFICATION FOR ADMIN
          setTimeout(() => {
            alert(`✅ PIPELINE COMPLETE\n\nDigital Elevation Model for ${form.district || form.state} has been processed.\nTopographic features extracted and terrain classification successful.`);
          }, 300);
        } else if (data.status === "failed") {
          ws.close();
          clearInterval(iv);
        }
      };
    } catch (err) {
      setError(err.response?.data?.error || err.message || "Upload failed");
      setJobStatus("failed");
    } finally {
      setLoading(false);
    }
  };

  const _fetchTerrainStats = async (id) => {
    try {
      const d = await getJob(id);
      // Parse terrain stats from job log if embedded
      if (d.job?.terrain_class_stats) setTerrainStats(d.job.terrain_class_stats);
    } catch (_) {}
  };

  const currentStage = () => {
    if (!jobStatus) return -1;
    if (jobStatus === "failed")  return -1;
    if (jobStatus === "uploading") return 0;
    if (progress < 60)  return 1;
    if (progress < 95)  return 2;
    return 3;
  };

  const reset = () => {
    setFile(null); setJobId(null); setJobStatus(null);
    setProgress(0); setLog(""); setTerrainStats(null);
    setUploadPct(0); setError("");
  };

  const stageIdx = currentStage();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div>
        <h2 className="t-heading">DEM Upload</h2>
        <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
          Upload a Digital Elevation Model for a state or district. The system will automatically
          extract topographic features and classify terrain into 12 classes.
        </p>
      </div>

      {/* ─── Region Selector ─────────────────────── */}
      <div style={card}>
        <h3 style={cardTitle}>Select Region</h3>
        <p style={hint}>DEM upload is available at State or District level only.</p>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 16 }}>
          <div>
            <label style={label}>State *</label>
            <select
              style={select}
              value={form.state}
              onChange={e => setForm(f => ({ ...f, state: e.target.value }))}
            >
              <option value="">— Select State —</option>
              {states.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label style={label}>District <span style={{ color: "#999" }}>(optional)</span></label>
            <select
              style={{ ...select, opacity: districts.length ? 1 : 0.5 }}
              value={form.district}
              disabled={!districts.length}
              onChange={e => setForm(f => ({ ...f, district: e.target.value }))}
            >
              <option value="">— All Districts (State-level DEM) —</option>
              {districts.map(d => <option key={d.name} value={d.name}>{d.name}</option>)}
            </select>
          </div>
        </div>
        {form.state && (
          <div style={{ marginTop: 12, padding: "10px 14px", background: "#f0f9ff",
            borderRadius: 10, fontSize: 13, color: "#0071e3" }}>
            📍 Selected: <strong>{form.district || form.state}</strong>
            {" "}({form.district ? "District-level DEM" : "State-level DEM"})
          </div>
        )}
      </div>

      {/* ─── File Upload ─────────────────────────── */}
      {!jobId && (
        <div style={card}>
          <h3 style={cardTitle}>Upload DEM File</h3>
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => {
              e.preventDefault(); setDragOver(false);
              const f = e.dataTransfer.files[0];
              if (f) setFile(f);
            }}
            style={{
              border: `2px dashed ${dragOver ? "#0071e3" : file ? "#34c759" : "#d1d1d6"}`,
              borderRadius: 16, padding: "40px 24px", textAlign: "center",
              background: dragOver ? "#f0f9ff" : file ? "#f0fff4" : "#fafafa",
              cursor: "pointer", transition: "all 0.2s",
              marginTop: 16
            }}
            onClick={() => document.getElementById("dem-file-input").click()}
          >
            <div style={{ fontSize: 36, marginBottom: 12 }}>{file ? "✅" : "⛰️"}</div>
            {file ? (
              <>
                <p style={{ fontWeight: 700, fontSize: 15 }}>{file.name}</p>
                <p style={{ color: "#666", fontSize: 13, marginTop: 4 }}>
                  {(file.size / (1024 * 1024)).toFixed(1)} MB
                </p>
                <button
                  onClick={e => { e.stopPropagation(); setFile(null); }}
                  style={{ marginTop: 10, ...btnSmall }}
                >Remove</button>
              </>
            ) : (
              <>
                <p style={{ fontWeight: 600, fontSize: 15 }}>Drop DEM file here</p>
                <p style={{ color: "#888", fontSize: 13, marginTop: 4 }}>
                  Supported: GeoTIFF (.tif), ASCII Grid (.asc), .img, .zip
                </p>
              </>
            )}
          </div>
          <input
            id="dem-file-input" type="file" hidden
            accept=".tif,.tiff,.asc,.img,.zip"
            onChange={e => setFile(e.target.files[0])}
          />

          {error && (
            <div style={{ marginTop: 12, padding: "10px 14px", background: "#fff0f0",
              borderRadius: 10, color: "#ff3b30", fontSize: 13 }}>⚠️ {error}</div>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading || !file || !form.state}
            style={{
              marginTop: 20, width: "100%", padding: "14px",
              background: (loading || !file || !form.state) ? "#e5e5e7" : "#000",
              color: (loading || !file || !form.state) ? "#aaa" : "#fff",
              border: "none", borderRadius: 12, fontSize: 15, fontWeight: 600,
              cursor: (loading || !file || !form.state) ? "not-allowed" : "pointer",
              fontFamily: "inherit", transition: "all 0.2s"
            }}
          >
            {loading ? "Uploading..." : "⛰️ Upload & Process DEM"}
          </button>
        </div>
      )}

      {/* ─── Pipeline Progress ───────────────────── */}
      {jobId && (
        <div style={card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
            <h3 style={cardTitle}>Processing Pipeline</h3>
            {jobStatus === "done" && (
              <button onClick={reset} style={{ ...btnSmall, background: "#000", color: "#fff" }}>
                + Upload Another
              </button>
            )}
          </div>

          {/* Stage timeline */}
          <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
            {STAGES.map((stage, i) => {
              const done    = stageIdx > i || jobStatus === "done";
              const active  = stageIdx === i;
              const pending = stageIdx < i && jobStatus !== "done";
              return (
                <div key={stage.id} style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 32, flexShrink: 0 }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      background: jobStatus === "failed" && active ? "#ff3b30"
                        : done ? "#34c759" : active ? "#0071e3" : "#e5e5e7",
                      color: (done || active) ? "#fff" : "#aaa",
                      fontSize: done ? 14 : 12, fontWeight: 700,
                      transition: "all 0.3s"
                    }}>
                      {done ? "✓" : active ? <Spinner /> : i + 1}
                    </div>
                    {i < STAGES.length - 1 && (
                      <div style={{
                        width: 2, flex: 1, minHeight: 24,
                        background: done ? "#34c759" : "#e5e5e7",
                        margin: "4px 0"
                      }} />
                    )}
                  </div>
                  <div style={{ paddingBottom: i < STAGES.length - 1 ? 24 : 0, flex: 1 }}>
                    <p style={{ fontWeight: 600, fontSize: 14,
                      color: done ? "#1a7a35" : active ? "#0071e3" : "#aaa" }}>
                      {stage.label}
                    </p>
                    {active && (
                      <div style={{ marginTop: 8 }}>
                        <div style={{ height: 4, background: "#e5e5e7", borderRadius: 4, overflow: "hidden" }}>
                          <div style={{
                            height: "100%", borderRadius: 4, transition: "width 0.5s",
                            background: "#0071e3",
                            width: `${Math.max(5, progress)}%`
                          }} />
                        </div>
                        <p style={{ fontSize: 12, color: "#666", marginTop: 6 }}>{progress}%</p>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Log */}
          {log && (
            <div style={{
              marginTop: 16, padding: "16px 20px",
              background: "#1e1e1e", borderRadius: 12,
              fontSize: 13, fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              color: "#d4d4d4",
              height: 250, overflowY: "auto", whiteSpace: "pre-wrap",
              border: "1px solid #333",
              boxShadow: "inset 0 2px 4px rgba(0,0,0,0.2)"
            }}>{log}</div>
          )}

          {/* Failed */}
          {jobStatus === "failed" && (
            <div style={{ marginTop: 14, padding: "12px 14px", background: "#fff0f0",
              borderRadius: 10, color: "#ff3b30", fontSize: 13 }}>
              ❌ Processing failed. Check log above.
              <button onClick={reset} style={{ marginLeft: 12, ...btnSmall }}>Try Again</button>
            </div>
          )}
        </div>
      )}

      {/* ─── Terrain Classification Result ──────── */}
      {jobStatus === "done" && terrainStats && (
        <TerrainResultCard stats={terrainStats} />
      )}
      {jobStatus === "done" && !terrainStats && (
        <div style={{ ...card, textAlign: "center", color: "#34c759" }}>
          <div style={{ fontSize: 36 }}>✅</div>
          <p style={{ fontWeight: 700, marginTop: 8 }}>Pipeline Complete</p>
          <p style={{ color: "#666", fontSize: 13 }}>
            DEM processed, topographic features extracted, and terrain classified successfully.
          </p>
        </div>
      )}
    </div>
  );
}

function TerrainResultCard({ stats }) {
  const sorted = Object.entries(stats)
    .map(([id, s]) => ({ id: parseInt(id), ...s }))
    .sort((a, b) => b.pct - a.pct);

  const dominant = sorted[0];

  return (
    <div style={card}>
      <h3 style={cardTitle}>🗺️ Terrain Classification Result</h3>
      <div style={{ marginTop: 12, marginBottom: 20, padding: "14px 18px",
        background: "#f0fdf4", borderRadius: 12, display: "flex", gap: 16, alignItems: "center" }}>
        <div style={{ width: 14, height: 14, borderRadius: "50%",
          background: TERRAIN_CLASSES[dominant?.id]?.color || "#666", flexShrink: 0 }} />
        <div>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            Dominant: Class {dominant?.id} — {dominant?.name}
          </div>
          <div style={{ color: "#666", fontSize: 13 }}>Covers {dominant?.pct}% of the region</div>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {sorted.map(cls => {
          const tc = TERRAIN_CLASSES[cls.id] || { name: cls.name, color: "#888" };
          return (
            <div key={cls.id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ width: 10, height: 10, borderRadius: "50%",
                background: tc.color, flexShrink: 0 }} />
              <div style={{ width: 130, fontSize: 12, fontWeight: 500, color: "#444" }}>
                {tc.name}
              </div>
              <div style={{ flex: 1, height: 8, background: "#f0f0f1",
                borderRadius: 4, overflow: "hidden" }}>
                <div style={{
                  height: "100%", borderRadius: 4,
                  background: tc.color,
                  width: `${cls.pct}%`
                }} />
              </div>
              <div style={{ width: 44, textAlign: "right", fontSize: 12, color: "#666" }}>
                {cls.pct}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const Spinner = () => (
  <span style={{
    display: "inline-block", width: 12, height: 12,
    border: "2px solid rgba(255,255,255,0.3)", borderTopColor: "#fff",
    borderRadius: "50%", animation: "spin 0.8s linear infinite"
  }} />
);

// ─── Styles ───────────────────────────────────────────────────────────────────
const card = {
  background: "#fff", border: "1px solid #e5e5e7",
  borderRadius: 20, padding: "24px 28px",
};
const cardTitle = { fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" };
const hint = { fontSize: 13, color: "#888", marginTop: 4 };
const label = { display: "block", fontSize: 12, fontWeight: 600, color: "#555",
  letterSpacing: "0.04em", marginBottom: 6, textTransform: "uppercase" };
const select = {
  width: "100%", padding: "10px 14px", borderRadius: 10,
  border: "1px solid #d1d1d6", background: "#fff",
  fontSize: 14, fontFamily: "inherit", outline: "none",
  appearance: "none", cursor: "pointer"
};
const btnSmall = {
  padding: "6px 14px", borderRadius: 8, border: "1px solid #d1d1d6",
  background: "#fafafa", fontSize: 12, fontWeight: 600,
  cursor: "pointer", fontFamily: "inherit"
};
