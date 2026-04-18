import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  getStates, getDistricts, getTalukas,
  getDisasters, generateSusceptibility, getRegionTerrain
} from "../../lib/api";
import JobProgress from "./JobProgress";

const ALL_TERRAIN_CLASSES = [
  { id: 1,  name: "Coastal Lowland",     color: "#0096c7" },
  { id: 2,  name: "Floodplain",          color: "#48cae4" },
  { id: 3,  name: "Alluvial Plain",      color: "#90e0ef" },
  { id: 4,  name: "Valley / River Basin",color: "#2dc653" },
  { id: 5,  name: "Piedmont / Foothill", color: "#80b918" },
  { id: 6,  name: "Low Hill",            color: "#ffd60a" },
  { id: 7,  name: "High Hill",           color: "#f4a261" },
  { id: 8,  name: "Mountain",            color: "#e63946" },
  { id: 9,  name: "Plateau / Mesa",      color: "#9b5de5" },
  { id: 10, name: "Escarpment / Cliff",  color: "#c1121f" },
  { id: 11, name: "Arid Plain",          color: "#d4a373" },
  { id: 12, name: "Coastal Dune",        color: "#06d6a0" },
];

function pctToWeight(pct) {
  return parseFloat(Math.min(0.95, Math.max(0.1, pct / 100)).toFixed(2));
}

export default function SusceptibilityMapping({ regionsFlat = [], jobs = [] }) {
  const navigate = useNavigate();
  const [states,    setStates]    = useState([]);
  const [districts, setDistricts] = useState([]);
  const [talukas,   setTalukas]   = useState([]);
  const [sel, setSel] = useState({ state: "", district: "", taluka: "" });

  const [grouped,  setGrouped]  = useState({});
  const [disaster, setDisaster] = useState(null);

  // Terrain weights are auto-computed from coverage — NOT user-editable
  const [weights, setWeights] = useState({});
  const [terrainData,    setTerrainData]    = useState(null);
  const [terrainLoading, setTerrainLoading] = useState(false);
  const [terrainErr,     setTerrainErr]     = useState("");
  const [terrainClasses, setTerrainClasses] = useState([]);

  const [regionId,  setRegionId]  = useState(null);
  const [jobId,     setJobId]     = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [progress,  setProgress]  = useState(0);
  const [log,       setLog]       = useState("");
  const [error,     setError]     = useState("");
  const [loading,   setLoading]   = useState(false);

  const [step, setStep] = useState(1);

  useEffect(() => {
    const fetchS = () => {
      getStates().then(d => setStates(d.states || [])).catch(() => {});
    };
    fetchS();
    const iv = setInterval(fetchS, 5000);
    getDisasters().then(d => setGrouped(d.grouped || {})).catch(() => {});
    return () => clearInterval(iv);
  }, []);

  useEffect(() => {
    if (!sel.state) { setDistricts([]); return; }
    getDistricts(sel.state).then(d => setDistricts(d.districts || [])).catch(() => {});
    setSel(s => ({ ...s, district: "", taluka: "" }));
  }, [sel.state]);

  useEffect(() => {
    if (!sel.state || !sel.district) { setTalukas([]); return; }
    getTalukas(sel.state, sel.district).then(d => setTalukas(d.talukas || [])).catch(() => {});
    setSel(s => ({ ...s, taluka: "" }));
  }, [sel.district]);

  // Find region ID from regionsFlat
  useEffect(() => {
    if (!sel.state) { setRegionId(null); return; }
    const match = regionsFlat.find(r =>
      r.state === sel.state &&
      (!sel.district || r.district === sel.district)
    );
    setRegionId(match?.id || null);
  }, [sel, regionsFlat]);

  // When region changes, load terrain classification data from DB
  useEffect(() => {
    setTerrainData(null);
    setTerrainErr("");
    setTerrainClasses([]);
    setWeights({});
    if (!regionId) return;

    setTerrainLoading(true);
    getRegionTerrain(regionId)
      .then(data => {
        setTerrainData(data);
        const stats = data.class_stats || {};
        // Only show classes actually present in this region's DEM
        const present = ALL_TERRAIN_CLASSES.filter(tc => {
          const s = stats[String(tc.id)];
          return s && (s.pct > 0 || s.pixel_count > 0);
        });
        setTerrainClasses(present.length > 0 ? present : ALL_TERRAIN_CLASSES);

        // Auto-compute weights from coverage — admin cannot change these
        const w = {};
        (present.length > 0 ? present : ALL_TERRAIN_CLASSES).forEach(tc => {
          const s = stats[String(tc.id)];
          w[tc.id] = s?.pct > 0 ? pctToWeight(s.pct) : 0.5;
        });
        setWeights(w);
      })
      .catch(() => {
        setTerrainErr("Terrain classification not yet available for this region.");
        setTerrainClasses(ALL_TERRAIN_CLASSES);
        // Default weights
        const w = {};
        ALL_TERRAIN_CLASSES.forEach(tc => { w[tc.id] = 0.5; });
        setWeights(w);
      })
      .finally(() => setTerrainLoading(false));
  }, [regionId]);

  const handleGenerate = async () => {
    if (!regionId) return setError("Region not found in database. Upload a DEM for this region first.");
    if (!disaster) return setError("Select a disaster type.");
    setError(""); setLoading(true);
    setJobStatus("pending"); setProgress(0); setLog("");

    try {
      const res = await generateSusceptibility(regionId, disaster.code, weights);
      setJobId(res.jobId);
      setJobStatus("processing");

      const wsBase = (window.location.protocol === "https:" ? "wss:" : "ws:")
        + "//" + window.location.host;
      const ws = new WebSocket(`${wsBase}/ws?job=${res.jobId}`);
      ws.onmessage = e => {
        const d = JSON.parse(e.data);
        setProgress(d.progress ?? 0);
        setLog(d.log ?? "");
        setJobStatus(d.status);
        if (d.status === "done" || d.status === "failed") ws.close();
      };
      ws.onerror = () => ws.close();
    } catch (err) {
      setError(err.response?.data?.error || err.message);
      setJobStatus("failed");
    } finally {
      setLoading(false);
    }
  };

  const classStats = terrainData?.class_stats || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div>
        <h2 className="t-heading">Susceptibility Generation</h2>
        <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
          Generate disaster-wise susceptibility maps using terrain classification + manual data.
        </p>
      </div>

      {/* Step tabs */}
      <div style={{ display: "flex", gap: 0, background: "#f5f5f7", borderRadius: 14, padding: 4 }}>
        {[
          { n: 1, label: "Select Region" },
          { n: 2, label: "Disaster Type" },
          { n: 3, label: "Terrain Weights" },
          { n: 4, label: "Generate" },
        ].map(s => (
          <button key={s.n} onClick={() => setStep(s.n)} style={{
            flex: 1, padding: "10px 8px", borderRadius: 10, border: "none",
            background: step === s.n ? "#fff" : "transparent",
            fontFamily: "inherit", fontSize: 13, fontWeight: step === s.n ? 700 : 400,
            color: step === s.n ? "#000" : "#666", cursor: "pointer",
            boxShadow: step === s.n ? "0 1px 4px rgba(0,0,0,0.08)" : "none",
            transition: "all 0.2s"
          }}>
            <span style={{ fontSize: 11, marginRight: 4 }}>{s.n}.</span>{s.label}
          </button>
        ))}
      </div>

      {/* ─── Step 1: Region ─────────────── */}
      {step === 1 && (
        <div style={card}>
          <h3 style={cardTitle}>Select Region</h3>
          <p style={hint}>Select a State and District to load terrain data and generate a susceptibility map.</p>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14, marginTop: 16 }}>
            <div>
              <label style={lbl}>State *</label>
              <select style={selectSt} value={sel.state}
                onChange={e => setSel(s => ({ ...s, state: e.target.value }))}>
                <option value="">— Select State —</option>
                {states.map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
              </select>
            </div>
            <div>
              <label style={lbl}>District</label>
              <select style={{ ...selectSt, opacity: districts.length ? 1 : 0.5 }}
                value={sel.district} disabled={!districts.length}
                onChange={e => setSel(s => ({ ...s, district: e.target.value }))}>
                <option value="">— Select District —</option>
                {districts.map(d => <option key={d.name} value={d.name}>{d.name}</option>)}
              </select>
            </div>
            <div>
              <label style={lbl}>Taluka <span style={{ color: "#999", textTransform: "none" }}>(optional)</span></label>
              <select style={{ ...selectSt, opacity: talukas.length ? 1 : 0.5 }}
                value={sel.taluka} disabled={!talukas.length}
                onChange={e => setSel(s => ({ ...s, taluka: e.target.value }))}>
                <option value="">— Select Taluka —</option>
                {talukas.map(t => <option key={t.name} value={t.name}>{t.name}</option>)}
              </select>
            </div>
          </div>

          {sel.state && (
            <div style={{ marginTop: 14, padding: "10px 14px", background: "#f0f9ff",
              borderRadius: 10, fontSize: 13, color: "#0071e3" }}>
              📍 Region: <strong>
                {[sel.taluka, sel.district, sel.state].filter(Boolean).join(" → ")}
              </strong>
              {regionId
                ? <span style={{ marginLeft: 8, color: "#34c759" }}>✓ Region found in database</span>
                : <span style={{ marginLeft: 8, color: "#ff9500" }}>⚠ Upload a DEM for this region first</span>
              }
            </div>
          )}

          {/* Terrain preview */}
          {terrainLoading && (
            <div style={{ marginTop: 12, padding: "10px 14px", background: "#f5f5f7",
              borderRadius: 10, fontSize: 13, color: "#666" }}>
              ⏳ Loading terrain classification data...
            </div>
          )}
          {terrainData && !terrainLoading && (
            <div style={{ marginTop: 12, padding: "12px 16px", background: "#f0fdf4",
              border: "1px solid #bbf7d0", borderRadius: 10 }}>
              <p style={{ fontSize: 12, fontWeight: 700, color: "#166534", marginBottom: 8 }}>
                🏔 TERRAIN CLASSIFICATION AVAILABLE
              </p>
              <p style={{ fontSize: 12, color: "#444", marginBottom: 8 }}>
                Dominant class: <strong>{terrainData.dominant_name}</strong>
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {terrainClasses.map(tc => {
                  const s = classStats[String(tc.id)];
                  return (
                    <span key={tc.id} style={{
                      display: "flex", alignItems: "center", gap: 5,
                      padding: "3px 8px", borderRadius: 20,
                      background: tc.color + "20", border: `1px solid ${tc.color}60`,
                      fontSize: 11, fontWeight: 600, color: "#333"
                    }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: tc.color }} />
                      {tc.name} {s?.pct > 0 ? `(${s.pct}%)` : ""}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
          {terrainErr && (
            <div style={{ marginTop: 10, padding: "8px 12px", background: "#fffbeb",
              borderRadius: 8, fontSize: 12, color: "#92400e" }}>
              ⚠ {terrainErr}
            </div>
          )}

          <button onClick={() => setStep(2)} disabled={!sel.state}
            style={{ ...btnPrimary, marginTop: 20, opacity: sel.state ? 1 : 0.4 }}>
            Next: Select Disaster →
          </button>
        </div>
      )}

      {/* ─── Step 2: Disaster Type ───────── */}
      {step === 2 && (
        <div style={card}>
          <h3 style={cardTitle}>Select Disaster Type</h3>
          <p style={hint}>Select which hazard to model. Each type uses a separate analysis script.</p>
          {Object.entries(grouped).map(([category, items]) => (
            <div key={category} style={{ marginTop: 20 }}>
              <p style={{ fontSize: 11, fontWeight: 700, color: "#999",
                letterSpacing: "0.08em", marginBottom: 10 }}>
                {category.toUpperCase()}
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
                {items.filter(d => d.is_active).map(d => (
                  <button key={d.code} onClick={() => { setDisaster(d); setStep(3); }} style={{
                    padding: "16px 14px", borderRadius: 14,
                    border: `2px solid ${disaster?.code === d.code ? d.color || "#000" : "#e5e5e7"}`,
                    background: disaster?.code === d.code ? (d.color || "#000") + "15" : "#fafafa",
                    cursor: "pointer", fontFamily: "inherit",
                    textAlign: "left", transition: "all 0.15s"
                  }}>
                    <div style={{ fontSize: 22 }}>{d.icon}</div>
                    <div style={{ fontWeight: 700, fontSize: 14, marginTop: 6 }}>{d.name}</div>
                    <div style={{ fontSize: 11, color: "#888", marginTop: 3, lineHeight: 1.4 }}>
                      {d.description?.substring(0, 60)}...
                    </div>
                    {d.script_path && (
                      <div style={{ marginTop: 6, fontSize: 10, color: "#0071e3", fontWeight: 600 }}>
                        ⚡ Custom script active
                      </div>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <button onClick={() => setStep(1)} style={btnSecondary}>← Back</button>
            <button onClick={() => setStep(3)} disabled={!disaster} style={{
              ...btnPrimary, opacity: disaster ? 1 : 0.4
            }}>Next: View Terrain Weights →</button>
          </div>
        </div>
      )}

      {/* ─── Step 3: Terrain Weights (READ-ONLY DISPLAY) ─────────────────── */}
      {step === 3 && disaster && (
        <div style={card}>
          <div style={{ display: "flex", gap: 14, alignItems: "flex-start", marginBottom: 16 }}>
            <span style={{ fontSize: 28 }}>{disaster.icon}</span>
            <div style={{ flex: 1 }}>
              <h3 style={cardTitle}>{disaster.name} — Terrain Weight Summary</h3>
              <p style={hint}>
                These weights are automatically derived from the terrain classification of this region's DEM.
                They represent the relative coverage of each terrain class and are applied during susceptibility computation.
              </p>
            </div>
            {/* Read-only badge */}
            <div style={{
              padding: "4px 12px", borderRadius: 20,
              background: "#f0f0f1", border: "1px solid #d1d1d6",
              fontSize: 11, fontWeight: 700, color: "#666",
              display: "flex", alignItems: "center", gap: 5, whiteSpace: "nowrap"
            }}>
              🔒 Read Only
            </div>
          </div>

          {/* Dominant + info banner */}
          {terrainData && (
            <div style={{
              marginBottom: 20, padding: "10px 16px",
              background: "#f0f9ff", border: "1px solid #bae6fd",
              borderRadius: 12, display: "flex", gap: 20,
              flexWrap: "wrap", alignItems: "center"
            }}>
              <div>
                <span style={{ fontSize: 11, color: "#0071e3", fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: "0.08em" }}>Dominant Terrain</span>
                <p style={{ fontWeight: 700, fontSize: 14, marginTop: 2 }}>{terrainData.dominant_name}</p>
              </div>
              <div>
                <span style={{ fontSize: 11, color: "#0071e3", fontWeight: 700,
                  textTransform: "uppercase", letterSpacing: "0.08em" }}>Classes Found</span>
                <p style={{ fontWeight: 700, fontSize: 14, marginTop: 2 }}>{terrainClasses.length} of 12</p>
              </div>
              <div style={{ fontSize: 12, color: "#0369a1", lineHeight: 1.4, flex: 1 }}>
                ℹ️ Weights are based on terrain coverage % from your DEM analysis.
                Higher coverage → higher influence on the susceptibility score.
              </div>
            </div>
          )}

          {/* Terrain weight bars — read-only */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {terrainClasses.length === 0 && terrainLoading && (
              <p style={{ color: "#888", fontSize: 13 }}>Loading terrain data...</p>
            )}
            {terrainClasses.map(tc => {
              const s = classStats[String(tc.id)];
              const pct  = s?.pct ?? 0;
              const w    = weights[tc.id] ?? 0;
              const barW = Math.round(w * 100);

              return (
                <div key={tc.id} style={{
                  display: "flex", alignItems: "center", gap: 12,
                  padding: "10px 14px", background: "#fafafa",
                  borderRadius: 10, border: "1px solid #f0f0f0"
                }}>
                  {/* Color dot + name */}
                  <div style={{
                    width: 10, height: 10, borderRadius: "50%",
                    background: tc.color, flexShrink: 0
                  }} />
                  <div style={{ width: 175, flexShrink: 0 }}>
                    <span style={{ fontSize: 13, fontWeight: 600 }}>
                      <span style={{ color: "#bbb", fontSize: 11 }}>{tc.id}.</span> {tc.name}
                    </span>
                    {pct > 0 && (
                      <span style={{ display: "block", fontSize: 10, color: "#888", marginTop: 1 }}>
                        Coverage: {pct}%
                      </span>
                    )}
                  </div>

                  {/* Read-only progress bar */}
                  <div style={{ flex: 1, height: 8, background: "#e5e5e7",
                    borderRadius: 4, overflow: "hidden", position: "relative" }}>
                    <div style={{
                      height: "100%", width: `${barW}%`,
                      background: tc.color,
                      borderRadius: 4, transition: "width 0.4s"
                    }} />
                  </div>

                  {/* Weight value badge */}
                  <div style={{
                    width: 44, textAlign: "center",
                    padding: "3px 8px", borderRadius: 6,
                    background: "#f0f0f1",
                    fontSize: 13, fontWeight: 700, color: "#333",
                    flexShrink: 0
                  }}>
                    {w.toFixed(2)}
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ marginTop: 20, padding: "10px 14px", background: "#fffbeb",
            border: "1px solid #fde68a", borderRadius: 10, fontSize: 12, color: "#92400e" }}>
            🔒 These terrain weights are system-generated from your DEM analysis and cannot be manually changed.
            To update them, re-process the DEM for this region with a new terrain classification.
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <button onClick={() => setStep(2)} style={btnSecondary}>← Back</button>
            <button onClick={() => setStep(4)} style={btnPrimary}>
              Next: Generate Map →
            </button>
          </div>
        </div>
      )}

      {/* ─── Step 4: Generate ─────────────── */}
      {step === 4 && (
        <div style={card}>
          <h3 style={cardTitle}>Generate Susceptibility Map</h3>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 16 }}>
            <InfoRow label="Region"
              value={[sel.taluka, sel.district, sel.state].filter(Boolean).join(" → ")} />
            <InfoRow label="Disaster"
              value={disaster ? `${disaster.icon} ${disaster.name}` : "—"} />
            <InfoRow label="Terrain Classes"
              value={`${terrainClasses.length} classes found in DEM`} />
            <InfoRow label="Dominant Terrain"
              value={terrainData?.dominant_name || "—"} />
          </div>

          {error && (
            <div style={{ marginTop: 14, padding: "12px 14px", background: "#fff0f0",
              borderRadius: 10, color: "#ff3b30", fontSize: 13 }}>
              ⚠️ {error}
            </div>
          )}

          {jobStatus && jobStatus !== "failed" && (
            <div style={{ marginTop: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13,
                fontWeight: 600, marginBottom: 6 }}>
                <span style={{ color: jobStatus === "done" ? "#34c759" : "#0071e3" }}>
                  {jobStatus === "done" ? "✅ Generation Complete" : "⚙️ Generating..."}
                </span>
                <span>{progress}%</span>
              </div>
              <div style={{ height: 8, background: "#e5e5e7", borderRadius: 4, overflow: "hidden" }}>
                <div style={{
                  height: "100%", borderRadius: 4, transition: "width 0.5s",
                  background: jobStatus === "done" ? "#34c759" : (disaster?.color || "#0071e3"),
                  width: `${progress}%`
                }} />
              </div>
              {log && (
                <div style={{
                  marginTop: 10, padding: "10px 12px",
                  background: "#f5f5f7", borderRadius: 8,
                  fontSize: 12, fontFamily: "monospace", color: "#555",
                  maxHeight: 160, overflowY: "auto", whiteSpace: "pre-wrap"
                }}>{log}</div>
              )}
            </div>
          )}

          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <button onClick={() => setStep(3)} style={btnSecondary}>← Back</button>
            <button
              onClick={handleGenerate}
              disabled={loading || !regionId || !disaster || jobStatus === "processing"}
              style={{
                ...btnPrimary,
                opacity: (loading || !regionId || !disaster || jobStatus === "processing") ? 0.5 : 1,
                background: disaster?.color || "#000"
              }}
            >
              {loading || jobStatus === "processing"
                ? "⚙️ Processing..."
                : jobStatus === "done"
                  ? "↺ Regenerate"
                  : `${disaster?.icon || "⚡"} Generate ${disaster?.name || ""} Map`}
            </button>
          </div>
        </div>
      )}

      {/* ─── Track History Table ─── */}
      <div className="divider" style={{ margin: "16px 0" }} />
      <div>
        <h3 className="t-heading" style={{ fontSize: 18 }}>Susceptibility Generation History</h3>
        <p style={{ fontSize: 13, color: "#666", marginTop: 4, marginBottom: 16 }}>
          Track recent susceptibility generation jobs. Click 'View Map' to visualize completed maps in the AHP Module.
        </p>

        <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 16, overflow: "hidden" }}>
          {jobs.filter(j => j.module === "susceptibility").length === 0 ? (
            <div style={{ padding: 32, textAlign: "center", color: "#999", fontSize: 14 }}>
              No generation jobs executed yet.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#fafafa", borderBottom: "1px solid #e5e5e7" }}>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Region</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Disaster Type</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Status</th>
                  <th style={{ padding: "12px 20px", textAlign: "center", color: "#666", fontWeight: 600 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.filter(j => j.module === "susceptibility").map((sj, i, arr) => {
                  const isDone = sj.status === "done";
                  const isPending = sj.status === "pending" || sj.status === "processing";
                  return (
                    <tr key={sj.id} style={{ borderBottom: i < arr.length - 1 ? "1px solid #f0f0f0" : "none" }}>
                      <td style={{ padding: "12px 20px", fontWeight: 500 }}>
                        {[sj.taluka, sj.district, sj.state].filter(Boolean).join(" → ")}
                      </td>
                      <td style={{ padding: "12px 20px", textTransform: "capitalize" }}>{sj.disaster_type}</td>
                      <td style={{ padding: "12px 20px" }}>
                        <span className="badge" style={{
                          background: isDone ? "rgba(52, 199, 89, 0.1)" : isPending ? "rgba(0, 113, 227, 0.1)" : "rgba(255, 59, 48, 0.1)",
                          color: isDone ? "#34c759" : isPending ? "#0071e3" : "#ff3b30",
                          border: isDone ? "1px solid rgba(52, 199, 89, 0.2)" : isPending ? "1px solid rgba(0, 113, 227, 0.2)" : "1px solid rgba(255, 59, 48, 0.2)"
                        }}>
                          {sj.status}
                        </span>
                      </td>
                      <td style={{ padding: "12px 20px", textAlign: "center", display: "flex", gap: 8, justifyContent: "center" }}>
                        <button className="btn btn-secondary btn-sm" onClick={() => setJobId(sj.id)}>👁️ View Log</button>
                        {isDone && (
                          <button 
                            className="btn btn-primary btn-sm" 
                            style={{ padding: "4px 10px", fontSize: 12 }}
                            onClick={() => navigate(`/susceptibility?district=${encodeURIComponent(sj.district)}&type=${encodeURIComponent(sj.disaster_type)}&state=${encodeURIComponent(sj.state)}&country=${encodeURIComponent(sj.country || 'India')}`)}
                          >
                            🗺️ View Map 
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        
        {jobId && !["pending", "processing"].includes(jobStatus) && step !== 4 && (
          <div style={{ position: "relative", marginTop: 24, zIndex: 10 }}>
            <button 
              onClick={() => { setJobId(null); setJobStatus(null); }}
              style={{ 
                position: "absolute", top: 10, right: 10, zIndex: 20,
                background: "none", border: "none", color: "#666", cursor: "pointer", fontSize: 18 
              }}
              title="Close Progress View"
            >
              ✕
            </button>
            <JobProgress jobId={jobId} onDone={() => {}} module="susceptibility" />
          </div>
        )}
      </div>

    </div>
  );
}

const InfoRow = ({ label, value }) => (
  <div style={{ padding: "10px 14px", background: "#f5f5f7", borderRadius: 10 }}>
    <p style={{ fontSize: 11, color: "#999", fontWeight: 600, letterSpacing: "0.04em",
      textTransform: "uppercase", marginBottom: 3 }}>{label}</p>
    <p style={{ fontSize: 14, fontWeight: 600 }}>{value}</p>
  </div>
);

const card = {
  background: "#fff", border: "1px solid #e5e5e7",
  borderRadius: 20, padding: "24px 28px",
};
const cardTitle = { fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" };
const hint = { fontSize: 13, color: "#888", marginTop: 4 };
const lbl  = {
  display: "block", fontSize: 11, fontWeight: 700, color: "#555",
  letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 6
};
const selectSt = {
  width: "100%", padding: "10px 14px", borderRadius: 10,
  border: "1px solid #d1d1d6", background: "#fff",
  fontSize: 14, fontFamily: "inherit", outline: "none",
};
const btnPrimary = {
  flex: 1, padding: "13px 20px", borderRadius: 12, border: "none",
  background: "#000", color: "#fff", fontSize: 14, fontWeight: 600,
  cursor: "pointer", fontFamily: "inherit", transition: "all 0.2s"
};
const btnSecondary = {
  padding: "13px 18px", borderRadius: 12, border: "1px solid #d1d1d6",
  background: "#fafafa", color: "#444", fontSize: 14, fontWeight: 600,
  cursor: "pointer", fontFamily: "inherit"
};
