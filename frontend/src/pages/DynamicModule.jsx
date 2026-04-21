import React, { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import { gsap } from "gsap";
import useStore from "../store/useStore";
import {
  getRegions, getDisasters,
  triggerDynamicPrediction, getDynamicResult,
  getDynamicHistory, getDynamicAvailableDates, getJob,
} from "../lib/api";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

/* ── Colour scales ──────────────────────────────────────────────────────────── */
const RISK_COLORS = {
  1: "#2dc653", 2: "#80b918", 3: "#f9c74f", 4: "#f3722c", 5: "#d62828",
};
const RISK_LABELS = {
  1: "Very Low", 2: "Low", 3: "Moderate", 4: "High", 5: "Very High",
};

function styleFeature(feat) {
  const cls = feat?.properties?.class_id;
  return {
    fillColor: RISK_COLORS[cls] || "#ccc",
    fillOpacity: 0.68,
    color: "#fff",
    weight: 0.8,
  };
}

/* ── Map helpers ────────────────────────────────────────────────────────────── */
function FitData({ data }) {
  const map = useMap();
  useEffect(() => {
    if (data?.features?.length) {
      try { map.fitBounds(L.geoJSON(data).getBounds(), { padding: [40, 40], maxZoom: 12 }); }
      catch { }
    }
  }, [data, map]);
  return null;
}

/* ── Spinner ────────────────────────────────────────────────────────────────── */
const Spinner = ({ size = 16, color = "#fff" }) => (
  <span style={{
    display: "inline-block", width: size, height: size,
    border: `2px solid rgba(255,255,255,0.25)`, borderTopColor: color,
    borderRadius: "50%", animation: "spin 0.8s linear infinite",
    flexShrink: 0,
  }} />
);

/* ── Progress bar ───────────────────────────────────────────────────────────── */
function ProgressBar({ value }) {
  return (
    <div style={{
      width: "100%", height: 6, background: "rgba(0,0,0,0.08)",
      borderRadius: 99, overflow: "hidden",
    }}>
      <div style={{
        height: "100%", width: `${Math.max(4, value)}%`,
        background: "linear-gradient(90deg, #0071e3, #34aadc)",
        borderRadius: 99,
        transition: "width 0.5s ease",
      }} />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════════
   MAIN PAGE
═══════════════════════════════════════════════════════════════════════════════ */
export default function DynamicModule() {
  const navigate  = useNavigate();
  const { user }  = useStore();
  const [searchParams] = useSearchParams();

  /* ── Region selectors ─────────────────────────────────────────────────────── */
  const [regions, setRegions]       = useState([]);
  const [disasterTypes, setDisasters] = useState([]);
  const [country, setCountry]       = useState(searchParams.get("country") || "");
  const [state, setState]           = useState(searchParams.get("state") || "");
  const [district, setDistrict]     = useState(searchParams.get("district") || "");
  const [disasterCode, setDisaster] = useState("landslide");
  const [selectedRegion, setSelectedRegion] = useState(null);

  /* ── Date selection ───────────────────────────────────────────────────────── */
  const [targetDate, setTargetDate]       = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 1);
    return d.toISOString().split("T")[0];
  });
  const [availDates, setAvailDates]       = useState([]);
  const [datesLoading, setDatesLoading]   = useState(false);

  /* ── Prediction state ─────────────────────────────────────────────────────── */
  const [predicting, setPredicting]       = useState(false);
  const [jobId, setJobId]                 = useState(null);
  const [jobProgress, setJobProgress]     = useState(0);
  const [jobLog, setJobLog]               = useState("");
  const [jobStatus, setJobStatus]         = useState(null);

  /* ── Result ───────────────────────────────────────────────────────────────── */
  const [result, setResult]               = useState(null);
  const [history, setHistory]             = useState([]);
  const [error, setError]                 = useState("");
  const [activeTab, setActiveTab]         = useState("predict"); // predict | history
  const pollRef = useRef(null);
  const sidebarRef = useRef();

  /* ── Load regions + disasters ─────────────────────────────────────────────── */
  useEffect(() => {
    getRegions().then(d => setRegions(d.countries || [])).catch(() => {});
    getDisasters().then(d => {
      const active = Object.values(d.grouped || {}).flat().filter(x => x.is_active);
      const relevant = active.filter(x =>
        ["landslide", "flood"].includes(x.code?.toLowerCase())
      );
      setDisasters(relevant);
      if (relevant.length) setDisaster(relevant[0].code.toLowerCase());
    }).catch(() => {});
  }, []);

  /* ── Sidebar entrance animation ──────────────────────────────────────────── */
  useEffect(() => {
    const ctx = gsap.context(() => {
      gsap.fromTo(sidebarRef.current,
        { x: 40, opacity: 0 },
        { x: 0, opacity: 1, duration: 0.6, ease: "power3.out", delay: 0.2 }
      );
    });
    return () => ctx.revert();
  }, []);

  /* ── Region cascades ─────────────────────────────────────────────────────── */
  const countries = regions.map(r => r.country);
  const stateObjs = regions.find(r => r.country === country)?.states || [];
  const states    = stateObjs.map(s => s.state).filter(Boolean);
  const distObjs  = stateObjs.find(s => s.state === state)?.districts || [];
  const districts = distObjs.map(d => d.district).filter(Boolean);

  /* ── When region selection changes, find regionId ────────────────────────── */
  useEffect(() => {
    if (!state) { setSelectedRegion(null); setAvailDates([]); return; }
    const d = distObjs.find(d => (d.district || "") === (district || ""));
    const s = stateObjs.find(s => s.state === state);
    const region = d || s;
    if (region?.id) {
      setSelectedRegion(region);
      setResult(null);
      setHistory([]);
    } else {
      setSelectedRegion(null);
    }
  }, [state, district, country]);

  /* ── Load available dates + history when region+disaster changes ─────────── */
  useEffect(() => {
    if (!selectedRegion?.id || !disasterCode) return;
    setDatesLoading(true);
    getDynamicAvailableDates(selectedRegion.id, disasterCode)
      .then(d => setAvailDates(d.available_dates || []))
      .catch(() => setAvailDates([]))
      .finally(() => setDatesLoading(false));
    getDynamicHistory(selectedRegion.id, disasterCode)
      .then(d => setHistory(d.predictions || []))
      .catch(() => setHistory([]));
  }, [selectedRegion, disasterCode]);

  /* ── Job polling ─────────────────────────────────────────────────────────── */
  const startPolling = useCallback((jid) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const job = await getJob(jid);
        setJobProgress(job.progress || 0);
        setJobLog(job.log || "");
        setJobStatus(job.status);
        if (["done", "failed"].includes(job.status)) {
          clearInterval(pollRef.current);
          setPredicting(false);
          if (job.status === "done") {
            // Fetch the result
            try {
              const res = await getDynamicResult(
                selectedRegion.id, disasterCode, targetDate
              );
              setResult(res);
              // Refresh history
              getDynamicHistory(selectedRegion.id, disasterCode)
                .then(d => setHistory(d.predictions || [])).catch(() => {});
            } catch { setError("Prediction completed but result not found. Try loading it manually."); }
          } else {
            setError("Prediction failed. Check the job log for details.");
          }
        }
      } catch { }
    }, 2500);
  }, [selectedRegion, disasterCode, targetDate]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  /* ── Trigger prediction ──────────────────────────────────────────────────── */
  const handlePredict = async () => {
    if (!selectedRegion?.id || !targetDate) return;
    setError(""); setResult(null); setPredicting(true); setJobProgress(0); setJobLog("");
    try {
      const resp = await triggerDynamicPrediction(
        selectedRegion.id, disasterCode, targetDate
      );
      setJobId(resp.jobId);
      startPolling(resp.jobId);
    } catch (e) {
      setError(e?.response?.data?.error || e.message);
      setPredicting(false);
    }
  };

  /* ── Load historical result ──────────────────────────────────────────────── */
  const handleLoadHistory = async (pred) => {
    setError(""); setResult(null);
    try {
      const res = await getDynamicResult(
        pred.region_id, pred.disaster_code,
        (pred.target_date || "").split("T")[0]
      );
      setResult(res);
      setTargetDate((pred.target_date || "").split("T")[0]);
      setActiveTab("predict");
    } catch (e) {
      setError("Could not load this prediction.");
    }
  };

  /* ── Render ──────────────────────────────────────────────────────────────── */
  const geojson = result?.risk_geojson;
  const classStats = result?.class_stats || {};
  const hasResult = geojson?.features?.length > 0;

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#f0f2f5" }}>

      {/* ── Top bar ──────────────────────────────────────────────────────────── */}
      <header style={{
        height: 64, padding: "0 28px", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        background: "rgba(255,255,255,0.92)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid #e5e5e7", zIndex: 200,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={() => navigate("/hub")} style={{
            background: "none", border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 8, color: "#666", fontSize: 13,
          }}>← Hub</button>
          <div style={{ width: 1, height: 20, background: "#e5e5e7" }} />
          <span style={{ fontSize: 15, fontWeight: 700 }}>Dynamic Risk Prediction</span>
          <span style={{
            fontSize: 11, padding: "3px 10px", borderRadius: 50,
            background: "linear-gradient(135deg,#667eea,#764ba2)",
            color: "#fff", fontWeight: 600, letterSpacing: "0.04em",
          }}>AI · Physics Engine</span>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {["predict", "history"].map(tab => (
            <button key={tab} onClick={() => setActiveTab(tab)} style={{
              padding: "7px 18px", borderRadius: 20, fontSize: 13, fontWeight: 600,
              border: "none", cursor: "pointer", transition: "all 0.2s",
              background: activeTab === tab ? "#000" : "transparent",
              color: activeTab === tab ? "#fff" : "#666",
            }}>{tab === "predict" ? "⚡ Predict" : "📋 History"}</button>
          ))}
        </div>
      </header>

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>

        {/* ── Map ────────────────────────────────────────────────────────────── */}
        <div style={{ flex: 1, position: "relative" }}>
          <MapContainer center={[20.5, 78.9]} zoom={5}
            style={{ width: "100%", height: "100%" }} zoomControl>
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='© <a href="https://openstreetmap.org">OpenStreetMap</a>'
            />
            {hasResult && <FitData data={geojson} />}
            {hasResult && (
              <GeoJSON
                key={`${result.region_id}-${result.target_date}`}
                data={geojson}
                style={styleFeature}
                onEachFeature={(feat, layer) => {
                  const cls = feat.properties?.class_id;
                  layer.bindTooltip(
                    `<strong style="color:${RISK_COLORS[cls]}">${RISK_LABELS[cls] || "N/A"} Risk</strong>`,
                    { permanent: false, direction: "top" }
                  );
                }}
              />
            )}
          </MapContainer>

          {/* Legend */}
          {hasResult && (
            <div style={{
              position: "absolute", bottom: 24, left: 16, zIndex: 999,
              background: "rgba(255,255,255,0.95)", backdropFilter: "blur(20px)",
              border: "1px solid #e5e5e7", borderRadius: 16, padding: "16px 20px",
              boxShadow: "0 8px 32px rgba(0,0,0,0.10)",
            }}>
              <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.1em", color: "#999", marginBottom: 10 }}>
                DYNAMIC RISK — {(result?.target_date || "").split("T")[0]}
              </p>
              {Object.entries(RISK_COLORS).map(([id, col]) => (
                <div key={id} style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 5 }}>
                  <div style={{ width: 12, height: 12, borderRadius: 3, background: col, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, color: "#333", flex: 1 }}>{RISK_LABELS[id]}</span>
                  <span style={{ fontSize: 11, color: "#999", fontVariantNumeric: "tabular-nums" }}>
                    {classStats[RISK_LABELS[id]]?.pct ?? "—"}%
                  </span>
                </div>
              ))}
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid #f0f0f0" }}>
                <p style={{ fontSize: 11, color: "#999" }}>Method: {result?.trigger_method}</p>
                <p style={{ fontSize: 11, color: "#999" }}>Composite: {result?.composite_mean?.toFixed(3)}</p>
              </div>
            </div>
          )}

          {/* Empty state overlay */}
          {!hasResult && !predicting && (
            <div style={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              background: "rgba(248,250,252,0.6)", backdropFilter: "blur(4px)",
              pointerEvents: "none", zIndex: 5,
            }}>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 48, marginBottom: 12 }}>🗺️</div>
                <p style={{ fontSize: 16, fontWeight: 600, color: "#333" }}>Select a region & date to predict</p>
                <p style={{ fontSize: 13, color: "#999", marginTop: 6 }}>
                  Dynamic risk map will appear here
                </p>
              </div>
            </div>
          )}

          {/* Predicting overlay */}
          {predicting && (
            <div style={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              background: "rgba(0,0,0,0.45)", backdropFilter: "blur(6px)",
              zIndex: 10,
            }}>
              <div style={{
                background: "#fff", borderRadius: 24, padding: "36px 40px",
                maxWidth: 380, width: "90%", boxShadow: "0 24px 80px rgba(0,0,0,0.2)",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 20 }}>
                  <Spinner size={24} color="#0071e3" />
                  <div>
                    <p style={{ fontWeight: 700, fontSize: 15 }}>Computing Risk…</p>
                    <p style={{ fontSize: 12, color: "#999" }}>{disasterCode} · {targetDate}</p>
                  </div>
                </div>
                <ProgressBar value={jobProgress} />
                <p style={{ fontSize: 11, color: "#999", marginTop: 8 }}>{jobProgress}% complete</p>
                {jobLog && (
                  <div style={{
                    marginTop: 14, padding: "10px 14px",
                    background: "#f8f9fa", borderRadius: 10,
                    fontSize: 11, color: "#555", fontFamily: "monospace",
                    maxHeight: 80, overflow: "auto", lineHeight: 1.6,
                  }}>
                    {jobLog.split("\n").slice(-4).join("\n")}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* ── Right sidebar ─────────────────────────────────────────────────── */}
        <div ref={sidebarRef} style={{
          width: 340, flexShrink: 0,
          background: "rgba(255,255,255,0.96)",
          backdropFilter: "blur(30px)",
          borderLeft: "1px solid #e5e5e7",
          display: "flex", flexDirection: "column",
          overflowY: "auto",
        }}>

          {/* PREDICT TAB */}
          {activeTab === "predict" && (
            <div style={{ padding: "24px 22px", display: "flex", flexDirection: "column", gap: 18 }}>
              <div>
                <h2 style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.02em" }}>
                  Dynamic Prediction
                </h2>
                <p style={{ fontSize: 12, color: "#999", marginTop: 4, lineHeight: 1.6 }}>
                  Fuses 10-day weather + susceptibility + LULC
                  using physics-based trigger models.
                </p>
              </div>

              {/* Disaster selector */}
              <div className="form-group">
                <label className="form-label">Disaster Type</label>
                <div style={{ display: "flex", gap: 8 }}>
                  {disasterTypes.map(d => (
                    <button key={d.code} onClick={() => setDisaster(d.code.toLowerCase())} style={{
                      flex: 1, padding: "9px 12px", borderRadius: 12, fontSize: 13,
                      fontWeight: 600, border: "none", cursor: "pointer", transition: "all 0.2s",
                      background: disasterCode === d.code.toLowerCase()
                        ? (d.code === "landslide" ? "linear-gradient(135deg,#8B4513,#A0522D)" : "linear-gradient(135deg,#1565C0,#1976D2)")
                        : "#f0f0f1",
                      color: disasterCode === d.code.toLowerCase() ? "#fff" : "#444",
                    }}>
                      {d.code === "landslide" ? "🏔️" : "🌊"} {d.name}
                    </button>
                  ))}
                </div>
                {/* Physics badge */}
                <div style={{
                  marginTop: 8, padding: "8px 12px", borderRadius: 10,
                  background: disasterCode === "landslide" ? "rgba(139,69,19,0.07)" : "rgba(21,101,192,0.07)",
                  fontSize: 11, color: disasterCode === "landslide" ? "#8B4513" : "#1565C0",
                }}>
                  {disasterCode === "landslide"
                    ? "⚙ TOPMODEL + Infinite-Slope Factor of Safety"
                    : "⚙ SCS-CN Runoff + ERA5 Surface Runoff + Soil Saturation"}
                </div>
              </div>

              {/* Region cascades */}
              <div className="form-group">
                <label className="form-label">Country</label>
                <select className="input" value={country}
                  onChange={e => { setCountry(e.target.value); setState(""); setDistrict(""); }}>
                  <option value="">Select country…</option>
                  {countries.map(c => <option key={c}>{c}</option>)}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">State</label>
                <select className="input" value={state} disabled={!country}
                  onChange={e => { setState(e.target.value); setDistrict(""); }}>
                  <option value="">Select state…</option>
                  {states.map(s => <option key={s}>{s}</option>)}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">District <span style={{ color: "#bbb", fontWeight: 400 }}>(optional)</span></label>
                <select className="input" value={district} disabled={!state}
                  onChange={e => setDistrict(e.target.value)}>
                  <option value="">State-level</option>
                  {districts.map(d => <option key={d}>{d}</option>)}
                </select>
              </div>

              {/* Date Picker */}
              <div className="form-group">
                <label className="form-label" style={{ display: "flex", justifyContent: "space-between" }}>
                  <span>Target Date</span>
                  {datesLoading && <Spinner size={12} color="#0071e3" />}
                  {availDates.length > 0 && (
                    <span style={{ fontSize: 11, color: "#34c759", fontWeight: 600 }}>
                      {availDates.length} dates available
                    </span>
                  )}
                </label>
                <input
                  type="date"
                  className="input"
                  value={targetDate}
                  onChange={e => setTargetDate(e.target.value)}
                  max={new Date().toISOString().split("T")[0]}
                  style={{ fontFamily: "inherit" }}
                />
                {availDates.length > 0 && (
                  <div style={{
                    marginTop: 6, padding: "8px 12px", borderRadius: 10,
                    background: "#f0fdf4", border: "1px solid #d1fae5",
                    fontSize: 11, color: "#065f46",
                  }}>
                    💡 Latest available: {availDates[0]}
                  </div>
                )}
                {selectedRegion && !datesLoading && availDates.length === 0 && (
                  <div style={{
                    marginTop: 6, padding: "8px 12px", borderRadius: 10,
                    background: "#fff7ed", border: "1px solid #fed7aa",
                    fontSize: 11, color: "#9a3412",
                  }}>
                    ⚠ No weather data found for this region. Download weather data first.
                  </div>
                )}
              </div>

              {error && (
                <div style={{
                  background: "rgba(255,59,48,0.06)", border: "1px solid rgba(255,59,48,0.2)",
                  borderRadius: 12, padding: "12px 16px", fontSize: 13, color: "#ff3b30",
                }}>
                  {error}
                </div>
              )}

              <button
                className="btn btn-primary"
                onClick={handlePredict}
                disabled={!selectedRegion || !targetDate || predicting}
                style={{
                  justifyContent: "center", gap: 10,
                  background: predicting ? "#999"
                    : disasterCode === "landslide"
                      ? "linear-gradient(135deg,#8B4513,#A0522D)"
                      : "linear-gradient(135deg,#1565C0,#1976D2)",
                }}
              >
                {predicting ? <><Spinner size={14} /> Computing…</> : "⚡ Run Dynamic Prediction"}
              </button>

              {/* Result summary */}
              {hasResult && !predicting && (
                <div style={{
                  background: "linear-gradient(135deg, rgba(52,199,89,0.06), rgba(0,113,227,0.06))",
                  border: "1px solid rgba(52,199,89,0.2)",
                  borderRadius: 16, padding: 16,
                }}>
                  <p style={{ fontSize: 11, fontWeight: 700, color: "#1a7a35", marginBottom: 10, letterSpacing: "0.06em" }}>
                    ✓ PREDICTION COMPLETE
                  </p>
                  <p style={{ fontSize: 12, color: "#555", marginBottom: 8 }}>
                    📅 {(result?.target_date || "").split("T")[0]} · {disasterCode}
                  </p>
                  <p style={{ fontSize: 12, color: "#666", marginBottom: 10 }}>
                    ⚙ {result?.trigger_method}
                  </p>
                  {/* Mini class distribution */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {[5, 4, 3, 2, 1].map(cls => {
                      const label = RISK_LABELS[cls];
                      const pct   = classStats[label]?.pct ?? 0;
                      return (
                        <div key={cls} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <div style={{ width: 8, height: 8, borderRadius: 2, background: RISK_COLORS[cls], flexShrink: 0 }} />
                          <span style={{ fontSize: 11, color: "#555", width: 70 }}>{label}</span>
                          <div style={{ flex: 1, height: 4, background: "#f0f0f0", borderRadius: 99 }}>
                            <div style={{
                              width: `${pct}%`, height: "100%",
                              background: RISK_COLORS[cls], borderRadius: 99,
                              transition: "width 0.8s ease",
                            }} />
                          </div>
                          <span style={{ fontSize: 11, color: "#999", width: 36, textAlign: "right" }}>
                            {pct.toFixed(1)}%
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  {result?.physics_meta?.mean_h_norm != null && (
                    <div style={{
                      marginTop: 10, paddingTop: 10, borderTop: "1px solid rgba(0,0,0,0.06)",
                      fontSize: 11, color: "#666",
                    }}>
                      💧 Soil: <strong>{result.physics_meta.soil_moisture_state}</strong>
                      {" "}(h/z = {result.physics_meta.mean_h_norm?.toFixed(3)})
                    </div>
                  )}
                  {result?.physics_meta?.mean_scscn_trigger != null && (
                    <div style={{ fontSize: 11, color: "#666", marginTop: 4 }}>
                      💧 SCS-CN trigger: <strong>{(result.physics_meta.mean_scscn_trigger * 100).toFixed(1)}%</strong>
                      {" "} · Soil sat: <strong>{(result.physics_meta.mean_soil_saturation * 100).toFixed(1)}%</strong>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* HISTORY TAB */}
          {activeTab === "history" && (
            <div style={{ padding: "24px 22px", display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <h2 style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.02em" }}>Prediction History</h2>
                <p style={{ fontSize: 12, color: "#999", marginTop: 4 }}>
                  Past dynamic risk predictions for this region.
                </p>
              </div>

              {!selectedRegion && (
                <div style={{ fontSize: 13, color: "#bbb", textAlign: "center", paddingTop: 20 }}>
                  Select a region first to see history.
                </div>
              )}

              {selectedRegion && history.length === 0 && (
                <div style={{
                  textAlign: "center", padding: "30px 20px",
                  background: "#f8f9fa", borderRadius: 16,
                }}>
                  <div style={{ fontSize: 32, marginBottom: 10 }}>📭</div>
                  <p style={{ fontSize: 13, color: "#666" }}>No predictions yet for this region.</p>
                </div>
              )}

              {history.map((pred) => {
                const dateStr = (pred.target_date || "").split("T")[0];
                const isHighRisk = (pred.class_stats?.["High"]?.pct + pred.class_stats?.["Very High"]?.pct) > 20;
                return (
                  <div
                    key={pred.id}
                    onClick={() => handleLoadHistory(pred)}
                    style={{
                      padding: "14px 16px", borderRadius: 14, cursor: "pointer",
                      border: "1px solid #e5e5e7",
                      background: "#fff",
                      transition: "all 0.2s",
                      boxShadow: "0 2px 8px rgba(0,0,0,0.04)",
                    }}
                    onMouseEnter={e => e.currentTarget.style.transform = "translateY(-2px)"}
                    onMouseLeave={e => e.currentTarget.style.transform = ""}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                      <div>
                        <p style={{ fontWeight: 700, fontSize: 14, marginBottom: 3 }}>📅 {dateStr}</p>
                        <p style={{ fontSize: 11, color: "#999" }}>{pred.disaster_code} · {pred.trigger_method}</p>
                      </div>
                      {isHighRisk && (
                        <span style={{
                          fontSize: 10, padding: "3px 8px", borderRadius: 99,
                          background: "#fff3f3", color: "#d62828", fontWeight: 700,
                        }}>HIGH RISK</span>
                      )}
                    </div>
                    <div style={{ marginTop: 8, display: "flex", gap: 6 }}>
                      {Object.entries(RISK_COLORS).map(([cls, col]) => {
                        const label = RISK_LABELS[cls];
                        const pct   = pred.class_stats?.[label]?.pct ?? 0;
                        return pct > 2 ? (
                          <div key={cls} title={`${label}: ${pct}%`} style={{
                            height: 6, borderRadius: 3,
                            background: col,
                            flex: pct,
                          }} />
                        ) : null;
                      })}
                    </div>
                    <p style={{ fontSize: 11, color: "#666", marginTop: 6 }}>
                      Composite: {pred.composite_mean?.toFixed(3)} · Click to view map →
                    </p>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
