import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  getStates, getDistricts, getTalukas,
  getDisasters, triggerDynamicPrediction, getDynamicAvailableDates
} from "../../lib/api";
import JobProgress from "./JobProgress";

export default function DynamicMapping({ regionsFlat = [], jobs = [] }) {
  const navigate = useNavigate();
  const logAnchorRef = useRef(null);
  const [states,    setStates]    = useState([]);
  const [districts, setDistricts] = useState([]);
  const [talukas,   setTalukas]   = useState([]);
  const [sel, setSel] = useState({ state: "", district: "", taluka: "" });

  const [grouped,  setGrouped]  = useState({});
  const [disaster, setDisaster] = useState(null);

  const [targetDate, setTargetDate] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 1);
    return d.toISOString().split("T")[0];
  });
  const [availDates, setAvailDates] = useState([]);
  const [datesLoading, setDatesLoading] = useState(false);

  const [regionId,  setRegionId]  = useState(null);
  const [jobId,     setJobId]     = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [progress,  setProgress]  = useState(0);
  const [log,       setLog]       = useState("");
  const [error,     setError]     = useState("");
  const [loading,   setLoading]   = useState(false);

  const [step, setStep] = useState(1);

  const handleViewLog = (id) => {
    setJobId(id);
    setJobStatus(null);
    setTimeout(() => {
      logAnchorRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);
  };

  // Filter ready regions
  const readyRegions = regionsFlat.filter(r => r.susceptibility_ready);
  const readyStates = [...new Set(readyRegions.map(r => r.state))];

  useEffect(() => {
    getStates().then(d => setStates(d.states ? d.states.filter(s => readyStates.includes(s.name)) : [])).catch(() => {});
    getDisasters().then(d => {
      // only active and landslide/flood logic applies for now
      const active = Object.values(d.grouped || {}).flat().filter(x => x.is_active && ["landslide", "flood"].includes(x.code.toLowerCase()));
      setGrouped({ "DYNAMIC DISASTERS": active });
    }).catch(() => {});
  }, [regionsFlat]);

  useEffect(() => {
    if (!sel.state) { setDistricts([]); return; }
    const distData = readyRegions.filter(r => r.state === sel.state && r.district).map(r => r.district);
    setDistricts([...new Set(distData)]);
    setSel(s => ({ ...s, district: "", taluka: "" }));
  }, [sel.state, regionsFlat]);

  useEffect(() => {
    if (!sel.state || !sel.district) { setTalukas([]); return; }
    const talData = readyRegions.filter(r => r.state === sel.state && r.district === sel.district && r.taluka).map(r => r.taluka);
    setTalukas([...new Set(talData)]);
    setSel(s => ({ ...s, taluka: "" }));
  }, [sel.district, regionsFlat]);

  // Find region ID
  useEffect(() => {
    if (!sel.state) { setRegionId(null); return; }
    const match = readyRegions.find(r =>
      r.state === sel.state &&
      (!sel.district || r.district === sel.district) &&
      (!sel.taluka || r.taluka === sel.taluka)
    );
    setRegionId(match?.id || null);
  }, [sel, readyRegions]);

  // Fetch dates when disaster and region selected
  useEffect(() => {
    if (!regionId || !disaster) return;
    setDatesLoading(true);
    getDynamicAvailableDates(regionId, disaster.code.toLowerCase())
      .then(d => setAvailDates(d.available_dates || []))
      .catch(() => setAvailDates([]))
      .finally(() => setDatesLoading(false));
  }, [regionId, disaster]);

  const handleGenerate = async () => {
    if (!regionId) return setError("Region not found or susceptibility not generated.");
    if (!disaster) return setError("Select a disaster type.");
    if (!targetDate) return setError("Select a target date.");
    
    setError(""); setLoading(true);
    setJobStatus("pending"); setProgress(0); setLog("");

    try {
      const res = await triggerDynamicPrediction(regionId, disaster.code.toLowerCase(), targetDate);
      setJobId(res.jobId);
      
      // If backend returned a cached fake job, skip the websocket loader
      if (res.message && res.message.includes("Existing")) {
        setJobStatus("done");
        setProgress(100);
        setLog(res.message + "\nLoaded from cache directly.");
        setLoading(false);
        return;
      }

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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
      <div>
        <h2 className="t-heading">Dynamic Risk Generation</h2>
        <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
          Run high-resolution physics-based risk models fusing susceptibility maps and selected daily weather.
        </p>
      </div>

      <div style={{ display: "flex", gap: 0, background: "#f5f5f7", borderRadius: 14, padding: 4 }}>
        {[
          { n: 1, label: "Select Region" },
          { n: 2, label: "Disaster & Date" },
          { n: 3, label: "Generate" },
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

      {step === 1 && (
        <div style={card}>
          <h3 style={cardTitle}>Select Ready Region</h3>
          <p style={hint}>Only regions where Susceptibility is generated are visible.</p>
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
                {districts.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
          </div>
          <button onClick={() => setStep(2)} disabled={!sel.state}
            style={{ ...btnPrimary, marginTop: 20, opacity: sel.state ? 1 : 0.4 }}>
            Next: Select Config →
          </button>
        </div>
      )}

      {step === 2 && (
        <div style={card}>
          <h3 style={cardTitle}>Select Disaster & Target Date</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10, marginTop: 16 }}>
            {grouped["DYNAMIC DISASTERS"]?.map(d => (
              <button key={d.code} onClick={() => setDisaster(d)} style={{
                padding: "16px 14px", borderRadius: 14,
                border: `2px solid ${disaster?.code === d.code ? "#000" : "#e5e5e7"}`,
                background: disaster?.code === d.code ? "#f4f4f5" : "#fafafa",
                cursor: "pointer", fontFamily: "inherit",
                textAlign: "left", transition: "all 0.15s"
              }}>
                <div style={{ fontSize: 22 }}>{d.icon}</div>
                <div style={{ fontWeight: 700, fontSize: 14, marginTop: 6 }}>{d.name}</div>
              </button>
            ))}
          </div>
          
          <div style={{ marginTop: 20 }}>
              <label style={lbl}>Target Date</label>
              <input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} style={selectSt} />
              {datesLoading && <p style={{fontSize: 11, color: '#0071e3', marginTop: 4}}>Loading date availability...</p>}
              {availDates.length > 0 && <p style={{fontSize: 11, color: '#34c759', marginTop: 4}}>✓ Weather data available for {availDates.length} recent dates.</p>}
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <button onClick={() => setStep(1)} style={btnSecondary}>← Back</button>
            <button onClick={() => setStep(3)} disabled={!disaster} style={{
              ...btnPrimary, opacity: disaster ? 1 : 0.4
            }}>Next: Generate Map →</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div style={card}>
          <h3 style={cardTitle}>Generate Dynamic Map</h3>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 16 }}>
            <InfoRow label="Region" value={[sel.district, sel.state].filter(Boolean).join(" → ")} />
            <InfoRow label="Disaster" value={disaster ? disaster.name : "—"} />
            <InfoRow label="Target Date" value={targetDate} />
          </div>

          {error && <div style={{ marginTop: 14, padding: "12px 14px", background: "#fff0f0", borderRadius: 10, color: "#ff3b30", fontSize: 13 }}>⚠️ {error}</div>}

          {jobStatus && jobStatus !== "failed" && (
            <div style={{ marginTop: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                <span style={{ color: jobStatus === "done" ? "#34c759" : "#0071e3" }}>
                  {jobStatus === "done" ? "✅ Generation Complete" : "⚙️ Generating..."}
                </span>
                <span>{progress}%</span>
              </div>
              <div style={{ height: 8, background: "#e5e5e7", borderRadius: 4, overflow: "hidden" }}>
                <div style={{ height: "100%", borderRadius: 4, transition: "width 0.5s", background: jobStatus === "done" ? "#34c759" : "#0071e3", width: `${progress}%` }} />
              </div>
              {log && (
                <div style={{ marginTop: 10, padding: "10px 12px", background: "#1a1a1a", borderRadius: 8, fontSize: 12, fontFamily: "monospace", color: "#34c759", maxHeight: 200, overflowY: "auto", whiteSpace: "pre-wrap" }}>
                  {log}
                </div>
              )}
            </div>
          )}

          <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
            <button onClick={() => setStep(2)} style={btnSecondary}>← Back</button>
            <button onClick={handleGenerate} disabled={loading || !regionId || !disaster || jobStatus === "processing"} style={{ ...btnPrimary, opacity: (loading || !regionId || !disaster || jobStatus === "processing") ? 0.5 : 1 }}>
              {loading || jobStatus === "processing" ? "⚙️ Processing..." : `${disaster?.icon || "⚡"} Generate map`}
            </button>
          </div>
        </div>
      )}

      {/* TRACK HISTORY */}
      <div className="divider" style={{ margin: "16px 0" }} />
      <div>
        <h3 className="t-heading" style={{ fontSize: 18 }}>Dynamic Generation History</h3>
        <p style={{ fontSize: 13, color: "#666", marginTop: 4, marginBottom: 16 }}>
          Track recent dynamic risk generation jobs.
        </p>

        <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 16, overflow: "hidden" }}>
          {jobs.filter(j => j.module === "dynamic").length === 0 ? (
            <div style={{ padding: 32, textAlign: "center", color: "#999", fontSize: 14 }}>No generation jobs executed yet.</div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#fafafa", borderBottom: "1px solid #e5e5e7" }}>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Region</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Details</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Status</th>
                  <th style={{ padding: "12px 20px", textAlign: "center", color: "#666", fontWeight: 600 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.filter(j => j.module === "dynamic").map((sj, i, arr) => {
                  const isDone = sj.status === "done";
                  const isPending = sj.status === "pending" || sj.status === "processing";
                  return (
                    <tr key={sj.id} style={{ borderBottom: i < arr.length - 1 ? "1px solid #f0f0f0" : "none" }}>
                      <td style={{ padding: "12px 20px", fontWeight: 500 }}>
                        {[sj.taluka, sj.district, sj.state].filter(Boolean).join(" → ")}
                      </td>
                      <td style={{ padding: "12px 20px", textTransform: "capitalize" }}>
                        <div style={{ fontWeight: 600 }}>{sj.disaster_type}</div>
                        <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>
                          📅 {sj.config?.targetDate || "N/A"}
                        </div>
                      </td>
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
                        <button className="btn btn-secondary btn-sm" onClick={() => handleViewLog(sj.id)}>👁️ View Log</button>
                        {isDone && (
                          <button 
                            className="btn btn-primary btn-sm" 
                            style={{ padding: "4px 10px", fontSize: 12 }}
                            onClick={() => navigate(`/dynamic?district=${encodeURIComponent(sj.district||"")}&state=${encodeURIComponent(sj.state||"")}`)}
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
        
        <div ref={logAnchorRef} />
        {jobId && (
          <div style={{ position: "relative", marginTop: 24, zIndex: 10 }}>
            <button onClick={() => { setJobId(null); setJobStatus(null); }} style={{ position: "absolute", top: 10, right: 10, zIndex: 20, background: "none", border: "none", color: "#666", cursor: "pointer", fontSize: 18 }} title="Close Progress View">✕</button>
            <JobProgress jobId={jobId} onDone={() => {}} module="dynamic" />
          </div>
        )}
      </div>

    </div>
  );
}

const InfoRow = ({ label, value }) => (
  <div style={{ padding: "10px 14px", background: "#f5f5f7", borderRadius: 10 }}>
    <p style={{ fontSize: 11, color: "#999", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", marginBottom: 3 }}>{label}</p>
    <p style={{ fontSize: 14, fontWeight: 600 }}>{value}</p>
  </div>
);

const card = { background: "#fff", border: "1px solid #e5e5e7", borderRadius: 20, padding: "24px 28px", };
const cardTitle = { fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" };
const hint = { fontSize: 13, color: "#888", marginTop: 4 };
const lbl  = { display: "block", fontSize: 11, fontWeight: 700, color: "#555", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 6 };
const selectSt = { width: "100%", padding: "10px 14px", borderRadius: 10, border: "1px solid #d1d1d6", background: "#fff", fontSize: 14, fontFamily: "inherit", outline: "none", };
const btnPrimary = { flex: 1, padding: "13px 20px", borderRadius: 12, border: "none", background: "#000", color: "#fff", fontSize: 14, fontWeight: 600, cursor: "pointer", fontFamily: "inherit", transition: "all 0.2s" };
const btnSecondary = { padding: "13px 18px", borderRadius: 12, border: "1px solid #d1d1d6", background: "#fafafa", color: "#444", fontSize: 14, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" };
