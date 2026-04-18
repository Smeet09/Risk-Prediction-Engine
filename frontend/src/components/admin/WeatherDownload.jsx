import { useState, useEffect } from "react";
import JobProgress from "./JobProgress";
// We use fetch directly in this component for ERA5 GEE tasks.

const WEATHER_FEATURES = [
  "Rainfall (IMERG: sum, max_intensity)",
  "Temperature (ERA5: mean, dewpoint)",
  "Pressure (ERA5: surface, sea_level)",
  "Wind (ERA5: speed, direction derived)",
  "Soil Moisture (ERA5-Land: layer 1 volumetric)",
  "Surface Runoff (ERA5-Land: mm derived)"
];

const YEARS = Array.from({ length: 15 }, (_, i) => new Date().getFullYear() - i);
const MONTHS = [
  { val: 1, label: "January" }, { val: 2, label: "February" },
  { val: 3, label: "March" }, { val: 4, label: "April" },
  { val: 5, label: "May" }, { val: 6, label: "June" },
  { val: 7, label: "July" }, { val: 8, label: "August" },
  { val: 9, label: "September" }, { val: 10, label: "October" },
  { val: 11, label: "November" }, { val: 12, label: "December" }
];

export default function WeatherDownload({ regionsFlat }) {
  const [country, setCountry] = useState("");
  const [state, setState] = useState("");
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [showFeatures, setShowFeatures] = useState(false);
  const [jobId, setJobId] = useState(null);
  const [statusMsg, setStatusMsg] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

  // Get unique countries
  const countries = [...new Set(regionsFlat.map(r => r.country).filter(Boolean))];

  // Get unique states for the selected country
  const statesInCountry = [...new Set(
    regionsFlat.filter(r => r.country === country).map(r => r.state).filter(Boolean)
  )];

  const selectedRegionInfo = regionsFlat.find(r => r.country === country && r.state === state);

  const [weatherStatuses, setWeatherStatuses] = useState([]);

  const fetchWeatherStatus = async () => {
    try {
      const res = await fetch("/api/weather/status", {
        headers: { "Authorization": `Bearer ${localStorage.getItem("aether_token")}` }
      });
      const data = await res.json();
      if (res.ok) setWeatherStatuses(data.statuses || []);
    } catch (err) {
      console.error("Failed to fetch weather status", err);
    }
  };

  useEffect(() => {
    fetchWeatherStatus();
    const iv = setInterval(fetchWeatherStatus, 3000);
    return () => clearInterval(iv);
  }, []);

  // Sync jobId with currently processing job for selection automatically if not set
  useEffect(() => {
    if (!jobId && weatherStatuses.length > 0) {
      const activeJob = weatherStatuses.find(ws => 
        (ws.status === "processing" || ws.status === "pending") &&
        ws.country === country && ws.state === state &&
        ws.year === year && ws.month === month
      );
      if (activeJob) {
        setJobId(activeJob.id);
      }
    }
  }, [weatherStatuses, country, state, year, month, jobId]);

  const handleDownload = async () => {
    if (!country || !state) {
      setErrorMsg("Please select a complete region (Country and State).");
      return;
    }
    setErrorMsg("");
    setStatusMsg("Starting job...");

    try {
      const response = await fetch("/api/weather/download", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${localStorage.getItem("aether_token")}`
        },
        body: JSON.stringify({ country, state, year, month })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Failed to start weather download.");

      setJobId(data.jobId);
      setStatusMsg("Job submitted");
    } catch (err) {
      setErrorMsg(err.message);
      setStatusMsg("");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <div>
        <h2 className="t-heading">Weather Data Download (ERA5/IMERG)</h2>
        <p style={{ fontSize: 14, color: "#666", marginTop: 6 }}>
          Download historical weather grids automatically aggregated to 2km resolution via Google Earth Engine. Data will be saved to Google Drive.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div className="form-group">
          <label className="form-label">Country *</label>
          <select className="input" value={country} onChange={e => { setCountry(e.target.value); setState(""); }}>
            <option value="">Select Country...</option>
            {countries.map(c => <option key={c}>{c}</option>)}
          </select>
        </div>

        <div className="form-group">
          <label className="form-label">State *</label>
          <select className="input" value={state} disabled={!country} onChange={e => setState(e.target.value)}>
            <option value="">Select State...</option>
            {statesInCountry.map(s => <option key={s}>{s}</option>)}
          </select>
        </div>

        <div className="form-group">
          <label className="form-label">Year *</label>
          <select className="input" value={year} onChange={e => setYear(Number(e.target.value))}>
            {YEARS.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
        </div>

        <div className="form-group">
          <label className="form-label">Month *</label>
          <select className="input" value={month} onChange={e => setMonth(Number(e.target.value))}>
            {MONTHS.map(m => <option key={m.val} value={m.val}>{m.label}</option>)}
          </select>
        </div>
      </div>

      <div style={{ background: "#f8f9ff", border: "1px solid #d0e1ff", borderRadius: 12, padding: "16px 20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" }} onClick={() => setShowFeatures(!showFeatures)}>
          <span style={{ fontWeight: 600, color: "#0071e3" }}>Included Weather Features (6 variables)</span>
          <span style={{ transform: showFeatures ? "rotate(180deg)" : "rotate(0)", transition: "transform 0.2s" }}>▼</span>
        </div>

        {showFeatures && (
          <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {WEATHER_FEATURES.map((feature, i) => (
              <div key={i} style={{ fontSize: 13, color: "#444", display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 6, height: 6, background: "#0071e3", borderRadius: "50%" }}></span>
                {feature}
              </div>
            ))}
          </div>
        )}
      </div>

      {errorMsg && (
        <div style={{ background: "rgba(255,59,48,0.06)", border: "1px solid rgba(255,59,48,0.15)", borderRadius: 12, padding: "12px 16px", fontSize: 13, color: "#ff3b30" }}>
          {errorMsg}
        </div>
      )}

      <button 
        className="btn btn-primary" 
        onClick={handleDownload} 
        disabled={!country || !state || !!jobId} 
        style={{ alignSelf: "flex-start" }}
      >
        {jobId ? "Viewing Job Progress..." : "Download Weather Data"}
      </button>

      {jobId && (
        <div style={{ position: "relative" }}>
          <button 
            onClick={() => setJobId(null)}
            style={{ 
              position: "absolute", top: 10, right: 10, zIndex: 10,
              background: "none", border: "none", color: "#666", cursor: "pointer", fontSize: 18 
            }}
            title="Close Progress View"
          >
            ✕
          </button>
          <JobProgress jobId={jobId} onDone={() => { }} module="weather" />
        </div>
      )}

      <div className="divider" style={{ margin: "24px 0" }} />

      <div>
        <h3 className="t-heading" style={{ fontSize: 18 }}>Weather Data Status Tracker</h3>
        <p style={{ fontSize: 13, color: "#666", marginTop: 4, marginBottom: 16 }}>
          Track which states already have ERA5/IMERG downloaded, and for what year/month. Pending jobs are processing in Earth Engine.
        </p>

        <div style={{ background: "#fff", border: "1px solid #e5e5e7", borderRadius: 16, overflow: "hidden" }}>
          {weatherStatuses.length === 0 ? (
            <div style={{ padding: 32, textAlign: "center", color: "#999", fontSize: 14 }}>
              No weather data downloaded yet.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "#fafafa", borderBottom: "1px solid #e5e5e7" }}>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Country</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>State</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Year</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Month</th>
                  <th style={{ padding: "12px 20px", textAlign: "left", color: "#666", fontWeight: 600 }}>Status</th>
                  <th style={{ padding: "12px 20px", textAlign: "center", color: "#666", fontWeight: 600 }}>Logs</th>
                </tr>
              </thead>
              <tbody>
                {weatherStatuses.map((ws, i) => {
                  const isDone = ws.status === "done";
                  const isPending = ws.status === "pending" || ws.status === "processing";
                  return (
                    <tr key={i} style={{ borderBottom: i < weatherStatuses.length - 1 ? "1px solid #f0f0f0" : "none" }}>
                      <td style={{ padding: "12px 20px" }}>{ws.country}</td>
                      <td style={{ padding: "12px 20px", fontWeight: 500 }}>{ws.state}</td>
                      <td style={{ padding: "12px 20px" }}>{ws.year}</td>
                      <td style={{ padding: "12px 20px" }}>
                        {MONTHS.find(m => m.val === ws.month)?.label || ws.month}
                      </td>
                      <td style={{ padding: "12px 20px" }}>
                        <span className="badge" style={{
                          background: isDone ? "rgba(52, 199, 89, 0.1)" : isPending ? "rgba(0, 113, 227, 0.1)" : "rgba(255, 59, 48, 0.1)",
                          color: isDone ? "#34c759" : isPending ? "#0071e3" : "#ff3b30",
                          border: isDone ? "1px solid rgba(52, 199, 89, 0.2)" : isPending ? "1px solid rgba(0, 113, 227, 0.2)" : "1px solid rgba(255, 59, 48, 0.2)"
                        }}>
                          {ws.status}
                        </span>
                      </td>
                      <td style={{ padding: "12px 20px", textAlign: "center" }}>
                        <button 
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            setCountry(ws.country);
                            setState(ws.state);
                            setYear(ws.year);
                            setMonth(ws.month);
                            setJobId(ws.id);
                          }}
                        >
                          👁️ View
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
