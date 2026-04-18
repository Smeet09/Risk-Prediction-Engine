import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { MapContainer, TileLayer, GeoJSON, useMap, Circle } from "react-leaflet";
import { getRegions, checkRainfall, getRainfallData, fetchEra5 } from "../lib/api";
import "leaflet/dist/leaflet.css";

function FlyTo({ center, zoom }) {
  const map = useMap();
  useEffect(() => {
    if (center) map.flyTo(center, zoom || 10, { duration: 1.8 });
  }, [center]);
  return null;
}

/* Rainfall circle markers — size = intensity */
function RainfallCircles({ data, center }) {
  if (!data || !center) return null;
  const maxRain = Math.max(...data.map(d => d.rainfall_mm), 1);
  return data.map((d, i) => {
    const radius = Math.max(2000, (d.rainfall_mm / maxRain) * 28000);
    const opacity = 0.15 + (d.rainfall_mm / maxRain) * 0.55;
    const color = d.rainfall_mm < 5 ? "#60a5fa" : d.rainfall_mm < 15 ? "#3b82f6" : d.rainfall_mm < 30 ? "#1d4ed8" : "#1e3a8a";
    return (
      <Circle
        key={i}
        center={[center[0] + (Math.random() - 0.5) * 0.05, center[1] + (Math.random() - 0.5) * 0.05]}
        radius={radius}
        pathOptions={{ fillColor: color, fillOpacity: opacity, color: "transparent", weight: 0 }}
      />
    );
  });
}

/* No-data overlay */
function NoDataOverlay() {
  return (
    <div style={{
      position: "absolute", inset: 0, zIndex: 999,
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      background: "rgba(248,248,249,0.82)",
      backdropFilter: "blur(16px)",
    }}>
      {/* Animated 3D no-data icon */}
      <div style={{
        width: 100, height: 100, borderRadius: "50%",
        background: "#fff", border: "1px solid #e5e5e7",
        display: "flex", alignItems: "center", justifyContent: "center",
        marginBottom: 24,
        boxShadow: "0 20px 60px rgba(0,0,0,0.1)",
        animation: "fadeUp 0.5s ease both",
      }}>
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none">
          <path d="M3 12h18M12 3l9 9-9 9-9-9 9-9" stroke="#ff3b30" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>
      <h3 style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.03em", marginBottom: 8 }}>
        No Data Available
      </h3>
      <p style={{ fontSize: 14, color: "#999", textAlign: "center", maxWidth: 280 }}>
        No ERA5 rainfall data exists for this region and date. Try fetching from ERA5 or select a different date.
      </p>
    </div>
  );
}

/* Time scrubber */
function TimeScrubber({ hour, setHour, data }) {
  if (!data?.length) return null;
  const maxRain = Math.max(...data.map(d => d.rainfall_mm), 0.1);
  return (
    <div style={{
      position: "absolute", bottom: 0, left: 0, right: 0, zIndex: 999,
      background: "rgba(255,255,255,0.92)", backdropFilter: "blur(20px)",
      borderTop: "1px solid #e5e5e7", padding: "16px 24px 12px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#000", minWidth: 80 }}>
          {String(hour).padStart(2, "0")}:00 UTC
        </div>
        <div style={{ flex: 1, position: "relative" }}>
          {/* Mini bar chart */}
          <div style={{
            display: "flex", alignItems: "flex-end", gap: 2, height: 28,
            marginBottom: 6, pointerEvents: "none"
          }}>
            {data.map((d, i) => (
              <div key={i} style={{
                flex: 1,
                height: `${Math.max(4, (d.rainfall_mm / maxRain) * 100)}%`,
                background: i === hour ? "#0071e3" : "#e5e5e7",
                borderRadius: "2px 2px 0 0",
                transition: "background 0.2s",
              }} />
            ))}
          </div>
          <input
            type="range" min={0} max={23} value={hour}
            onChange={e => setHour(Number(e.target.value))}
            style={{ width: "100%", accentColor: "#000", cursor: "pointer" }}
          />
        </div>
        <div style={{
          fontSize: 13, fontWeight: 600, minWidth: 90, textAlign: "right",
          color: data[hour]?.rainfall_mm > 20 ? "#ff3b30" : "#0071e3"
        }}>
          {data[hour]?.rainfall_mm?.toFixed(1) ?? "–"} mm
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "center", gap: 6 }}>
        {[0, 6, 12, 18, 23].map(h => (
          <span key={h} style={{ fontSize: 11, color: "#aaa" }}>{String(h).padStart(2, "0")}:00</span>
        ))}
      </div>
    </div>
  );
}

export default function RainfallModule() {
  const navigate = useNavigate();
  const [regions, setRegions] = useState([]);
  const [country, setCountry] = useState("");
  const [state, setState] = useState("");
  const [district, setDistrict] = useState("");
  const [selectedRegionId, setSelectedRegionId] = useState(null);
  const [mapCenter, setMapCenter] = useState(null);
  const [date, setDate] = useState(new Date().toISOString().split("T")[0]);
  const [status, setStatus] = useState("idle"); // idle | checking | no-data | loading | done | fetching
  const [timeseries, setTimeseries] = useState(null);
  const [hour, setHour] = useState(12);
  const [error, setError] = useState("");

  useEffect(() => {
    getRegions().then(d => setRegions(d.countries || [])).catch(() => {});
  }, []);

  const selectedCountryObj = regions.find(r => r.country === country);
  const states = selectedCountryObj?.states?.map(s => s.state).filter(Boolean) || [];
  const selectedStateObj = selectedCountryObj?.states?.find(s => s.state === state);
  const districts = (selectedStateObj?.districts || []).map(d => d.district).filter(Boolean);

  const handleDistrictChange = (val) => {
    setDistrict(val);
    const found = selectedStateObj?.districts?.find(d => d.district === val);
    if (found) {
      setSelectedRegionId(found.id);
      if (found.centroid?.coordinates)
        setMapCenter([found.centroid.coordinates[1], found.centroid.coordinates[0]]);
    }
  };

  const handleCheck = async () => {
    if (!selectedRegionId || !date) return;
    setError(""); setStatus("checking"); setTimeseries(null);
    try {
      const res = await checkRainfall(selectedRegionId, date);
      if (res.exists) {
        setStatus("loading");
        const data = await getRainfallData(selectedRegionId, date);
        setTimeseries(data.timeseries);
        setStatus("done");
        setHour(12);
      } else {
        setStatus("no-data");
      }
    } catch {
      setStatus("idle");
      setError("Failed to check data. Please try again.");
    }
  };

  const handleFetchEra5 = async () => {
    if (!selectedRegionId || !date) return;
    setStatus("fetching"); setError("");
    try {
      await fetchEra5(selectedRegionId, date);
      // Poll for data after a short delay
      setTimeout(async () => {
        const data = await getRainfallData(selectedRegionId, date);
        setTimeseries(data.timeseries);
        setStatus("done");
        setHour(12);
      }, 4000);
    } catch {
      setError("ERA5 fetch failed. Check your CDS API credentials.");
      setStatus("no-data");
    }
  };

  const currentRain = timeseries?.[hour];

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: "#f8f8f9" }}>
      {/* Header */}
      <header style={{
        height: 64, padding: "0 32px", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        background: "rgba(255,255,255,0.9)", backdropFilter: "blur(20px)",
        borderBottom: "1px solid #e5e5e7", zIndex: 100
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={() => navigate("/hub")} style={{
            background: "none", border: "none", cursor: "pointer",
            display: "flex", alignItems: "center", gap: 8, color: "#666", fontSize: 13
          }}>← Hub</button>
          <div style={{ width: 1, height: 20, background: "#e5e5e7" }} />
          <span style={{ fontSize: 15, fontWeight: 600 }}>Rainfall & Weather</span>
          <span style={{
            fontSize: 11, padding: "3px 10px", borderRadius: 50,
            background: "#eff6ff", color: "#0071e3", fontWeight: 500
          }}>ERA5 Engine</span>
        </div>
        {status === "done" && (
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 13, color: "#0071e3", fontWeight: 500
          }}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#0071e3",
              display: "inline-block", animation: "pulse 2s ease infinite" }} />
            Live Rainfall View
          </div>
        )}
      </header>

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left: Greyscale susceptibility-style map */}
        <div style={{ flex: 1, position: "relative" }}>
          <MapContainer center={[20.5, 78.9]} zoom={5}
            style={{ width: "100%", height: "100%" }}>
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='© <a href="https://openstreetmap.org">OpenStreetMap</a>'
              style={{ filter: "grayscale(100%)" }}
            />
            {mapCenter && <FlyTo center={mapCenter} zoom={10} />}
            {status === "done" && timeseries && (
              <RainfallCircles data={[timeseries[hour]]} center={mapCenter} />
            )}
          </MapContainer>

          {/* No data overlay */}
          {status === "no-data" && <NoDataOverlay />}

          {/* Loading overlay */}
          {(status === "checking" || status === "loading" || status === "fetching") && (
            <div style={{
              position: "absolute", inset: 0, zIndex: 999,
              display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              background: "rgba(248,248,249,0.75)", backdropFilter: "blur(12px)"
            }}>
              <div style={{
                width: 52, height: 52, borderRadius: "50%",
                border: "2px solid #e5e5e7", borderTopColor: "#0071e3",
                animation: "spin 0.8s linear infinite", marginBottom: 16
              }} />
              <p style={{ fontSize: 14, fontWeight: 500, color: "#444" }}>
                {status === "checking" ? "Checking data availability…"
                  : status === "fetching" ? "Fetching ERA5 data from Copernicus…"
                  : "Loading rainfall data…"}
              </p>
            </div>
          )}

          {/* Rainfall Intensity Indicator */}
          {status === "done" && currentRain && (
            <div style={{
              position: "absolute", top: 16, left: 16, zIndex: 999,
              background: "rgba(255,255,255,0.92)", backdropFilter: "blur(20px)",
              border: "1px solid #e5e5e7", borderRadius: 16, padding: "16px 20px",
              minWidth: 180
            }}>
              <p className="t-label" style={{ marginBottom: 8 }}>HOUR {String(hour).padStart(2, "0")}:00</p>
              <div style={{
                fontSize: 36, fontWeight: 700, letterSpacing: "-0.04em",
                color: currentRain.rainfall_mm > 20 ? "#ff3b30"
                     : currentRain.rainfall_mm > 5 ? "#0071e3" : "#000"
              }}>
                {currentRain.rainfall_mm?.toFixed(1)}<span style={{ fontSize: 16, color: "#999", fontWeight: 400 }}> mm</span>
              </div>
              <p style={{ fontSize: 12, color: "#999", marginTop: 4 }}>
                {currentRain.rainfall_mm < 1 ? "Trace / No rain"
                  : currentRain.rainfall_mm < 5 ? "Light rainfall"
                  : currentRain.rainfall_mm < 15 ? "Moderate rainfall"
                  : currentRain.rainfall_mm < 30 ? "Heavy rainfall"
                  : "Very heavy rainfall"}
              </p>
            </div>
          )}

          {/* 24h summary strip */}
          {status === "done" && timeseries && (
            <div style={{
              position: "absolute", top: 16, right: 16, zIndex: 999,
              background: "rgba(255,255,255,0.92)", backdropFilter: "blur(20px)",
              border: "1px solid #e5e5e7", borderRadius: 16, padding: "16px 20px"
            }}>
              <p className="t-label" style={{ marginBottom: 8 }}>24H TOTAL</p>
              <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.03em" }}>
                {timeseries.reduce((s, d) => s + (d.rainfall_mm || 0), 0).toFixed(1)}
                <span style={{ fontSize: 14, color: "#999", fontWeight: 400 }}> mm</span>
              </div>
              <p style={{ fontSize: 12, color: "#999", marginTop: 4 }}>
                Peak: {Math.max(...timeseries.map(d => d.rainfall_mm)).toFixed(1)} mm
              </p>
            </div>
          )}

          {/* Time Scrubber */}
          <TimeScrubber hour={hour} setHour={setHour} data={timeseries} />
        </div>

        {/* Right Control Panel */}
        <div style={{
          width: 320, flexShrink: 0,
          background: "rgba(255,255,255,0.9)", backdropFilter: "blur(30px)",
          borderLeft: "1px solid #e5e5e7",
          padding: "28px 24px", overflowY: "auto",
          display: "flex", flexDirection: "column", gap: 20
        }}>
          <div>
            <h2 style={{ fontSize: 18, fontWeight: 700, letterSpacing: "-0.02em" }}>
              Rainfall Query
            </h2>
            <p style={{ fontSize: 13, color: "#999", marginTop: 4 }}>
              Select region and date to load ERA5 data
            </p>
          </div>

          <div className="form-group">
            <label className="form-label">Country</label>
            <select className="input" value={country}
              onChange={e => { setCountry(e.target.value); setState(""); setDistrict(""); setStatus("idle"); }}>
              <option value="">Select country…</option>
              {regions.map(r => <option key={r.country}>{r.country}</option>)}
            </select>
          </div>

          <div className="form-group">
            <label className="form-label">State</label>
            <select className="input" value={state} disabled={!country}
              onChange={e => { setState(e.target.value); setDistrict(""); setStatus("idle"); }}>
              <option value="">Select state…</option>
              {states.map(s => <option key={s}>{s}</option>)}
            </select>
          </div>

          <div className="form-group">
            <label className="form-label">District</label>
            <select className="input" value={district} disabled={!state}
              onChange={e => handleDistrictChange(e.target.value)}>
              <option value="">Select district…</option>
              {districts.map(d => <option key={d}>{d}</option>)}
            </select>
          </div>

          <div className="form-group">
            <label className="form-label">Date</label>
            <input
              type="date" className="input" value={date}
              max={new Date().toISOString().split("T")[0]}
              onChange={e => { setDate(e.target.value); setStatus("idle"); setTimeseries(null); }}
            />
          </div>

          {error && (
            <div style={{
              background: "rgba(255,59,48,0.06)", border: "1px solid rgba(255,59,48,0.15)",
              borderRadius: 12, padding: "12px 16px", fontSize: 13, color: "#ff3b30"
            }}>{error}</div>
          )}

          <button
            className="btn btn-primary"
            onClick={handleCheck}
            disabled={!district || !date || status === "checking" || status === "loading"}
            style={{ justifyContent: "center" }}
          >
            Check & Load Rainfall
          </button>

          {status === "no-data" && (
            <div>
              <div style={{
                background: "#fff9f9", border: "1px solid rgba(255,59,48,0.2)",
                borderRadius: 14, padding: "16px", marginBottom: 12
              }}>
                <p style={{ fontSize: 13, color: "#ff3b30", fontWeight: 600, marginBottom: 6 }}>
                  No cached data found
                </p>
                <p style={{ fontSize: 12, color: "#666" }}>
                  Trigger an ERA5 download from Copernicus CDS for this region and date.
                </p>
              </div>
              <button
                className="btn btn-secondary"
                onClick={handleFetchEra5}
                style={{ width: "100%", justifyContent: "center" }}
              >
                ↓ Fetch from ERA5
              </button>
            </div>
          )}

          {status === "done" && timeseries && (
            <>
              <div className="divider" />
              <div>
                <p className="t-label" style={{ marginBottom: 12 }}>HOURLY BREAKDOWN</p>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {timeseries.map((entry, i) => {
                    const max = Math.max(...timeseries.map(d => d.rainfall_mm), 1);
                    return (
                      <div key={i}
                        onClick={() => setHour(i)}
                        style={{
                          display: "flex", alignItems: "center", gap: 10,
                          padding: "7px 10px", borderRadius: 10, cursor: "pointer",
                          background: i === hour ? "rgba(0,113,227,0.06)" : "transparent",
                          border: i === hour ? "1px solid rgba(0,113,227,0.15)" : "1px solid transparent",
                          transition: "all 0.15s"
                        }}
                      >
                        <span style={{ fontSize: 11, color: "#999", width: 36, flexShrink: 0 }}>
                          {String(i).padStart(2, "0")}:00
                        </span>
                        <div style={{ flex: 1, height: 6, background: "#f0f0f1", borderRadius: 3, overflow: "hidden" }}>
                          <div style={{
                            height: "100%", borderRadius: 3,
                            width: `${(entry.rainfall_mm / max) * 100}%`,
                            background: i === hour ? "#0071e3" : "#bcd4f0",
                            transition: "all 0.3s"
                          }} />
                        </div>
                        <span style={{ fontSize: 12, fontWeight: 500, color: i === hour ? "#0071e3" : "#666", width: 48, textAlign: "right" }}>
                          {entry.rainfall_mm?.toFixed(1)}mm
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
